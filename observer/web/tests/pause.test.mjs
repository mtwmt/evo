import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const mainPath = fileURLToPath(new URL("../src/main.ts", import.meta.url));
const mainSource = await readFile(mainPath, "utf8");
const compiledMain = ts.transpileModule(mainSource, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
  },
}).outputText;

class Emitter {
  listeners = new Map();

  on(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }

  once(name, callback) {
    const wrapper = (...args) => {
      this.listeners.set(name, (this.listeners.get(name) || []).filter((fn) => fn !== wrapper));
      callback(...args);
    };
    this.on(name, wrapper);
  }

  emit(name, ...args) {
    for (const callback of [...(this.listeners.get(name) || [])]) callback(...args);
  }
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.classList = {
      values: new Set(),
      add: (...names) => names.forEach((name) => this.classList.values.add(name)),
      remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
      contains: (name) => this.classList.values.has(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !this.classList.values.has(name) : Boolean(force);
        if (enabled) this.classList.values.add(name);
        else this.classList.values.delete(name);
        return enabled;
      },
    };
    this.style = {};
    this.value = "";
    this.textContent = "";
    this.disabled = false;
    this.hidden = false;
  }

  set innerHTML(_value) {
    this.children = [];
  }

  get innerHTML() {
    return "";
  }

  get options() {
    return this.children.filter((child) => child.tagName === "OPTION");
  }

  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }

  dispatch(name, event = {}) {
    for (const callback of [...(this.listeners.get(name) || [])]) {
      callback({ preventDefault() {}, ...event });
    }
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  appendChild(node) {
    this.children.push(node);
    return node;
  }

  insertBefore(node, before) {
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, node);
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  setPointerCapture() {}
  hasPointerCapture() { return false; }
  releasePointerCapture() {}
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function makeResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}

function makeHarness() {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeElement(id === "model-select" ? "select" : "div"));
    return elements.get(id);
  };
  const speedButtons = ["1", "2", "3"].map((speed) => {
    const button = new FakeElement("button");
    button.setAttribute("data-speed", speed);
    return button;
  });
  const documentListeners = new Map();
  const document = {
    body: new FakeElement("body"),
    activeElement: null,
    getElementById: getElement,
    querySelectorAll: (selector) => selector === ".speed-btn" ? speedButtons : [],
    createElement: (tagName) => new FakeElement(tagName),
    createTextNode: (text) => ({ textContent: text }),
    addEventListener(name, callback) {
      const callbacks = documentListeners.get(name) || [];
      callbacks.push(callback);
      documentListeners.set(name, callbacks);
    },
    dispatch(name, event = {}) {
      for (const callback of documentListeners.get(name) || []) {
        callback({ preventDefault() {}, target: null, ...event });
      }
    },
  };
  const timers = new Map();
  let nextTimerId = 1;
  const addTimer = (kind, callback, delay) => {
    const id = nextTimerId++;
    timers.set(id, { id, kind, callback, delay });
    return id;
  };
  const clearTimer = (id) => timers.delete(id);
  const requests = [];
  const sockets = [];
  const calls = [];
  const interactionModes = [];
  const spacePanStates = [];
  const gameEvents = new Emitter();
  const scene = {
    events: new Emitter(),
    setInteractionMode(mode) {
      interactionModes.push(mode);
      this.events.emit("interaction-mode-changed", mode);
    },
    setSpacePanActive(active) {
      spacePanStates.push(active);
    },
    zoomBy() {},
    resetZoom() {},
    updateScene() {},
  };
  const game = {
    events: gameEvents,
    scene: { getScene: () => scene },
  };

  class FakeWebSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    static CLOSED = 3;

    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.onmessage = null;
      this.onclose = null;
      this.closeCalls = [];
      sockets.push(this);
    }

    close(code, reason) {
      this.closeCalls.push({ code, reason });
      this.readyState = FakeWebSocket.CLOSED;
    }

    deliver(data) {
      this.readyState = FakeWebSocket.OPEN;
      this.onmessage?.({ data: JSON.stringify(data) });
    }
  }

  function fakeFetch(input, init = {}) {
    const request = {
      url: String(input),
      init,
      signal: init.signal,
      deferred: deferred(),
      settled: false,
    };
    calls.push(request);
    if (request.signal?.aborted) {
      request.settled = true;
      request.deferred.reject(new DOMException("Aborted", "AbortError"));
    } else {
      request.signal?.addEventListener("abort", () => {
        request.settled = true;
        request.deferred.reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    }
    requests.push(request);
    return request.deferred.promise;
  }

  const window = {
    addEventListener: document.addEventListener.bind(document),
    location: { protocol: "http:", port: "5173", host: "localhost:5173" },
    innerWidth: 1440,
    setInterval: (callback, delay) => addTimer("interval", callback, delay),
    clearInterval: clearTimer,
    setTimeout: (callback, delay) => addTimer("timeout", callback, delay),
    clearTimeout: clearTimer,
    prompt: () => null,
  };
  const Phaser = {
    AUTO: "AUTO",
    Game: class { constructor() { return game; } },
  };
  const context = {
    exports: {},
    require(specifier) {
      if (specifier === "phaser") return { default: Phaser };
      if (specifier === "./scenes/CosmicScene") return { CosmicScene: class {} };
      throw new Error(`Unexpected import: ${specifier}`);
    },
    document,
    window,
    WebSocket: FakeWebSocket,
    fetch: fakeFetch,
    AbortController,
    DOMException,
    URLSearchParams,
    Math,
    Date,
    JSON,
    Object,
    Array,
    Number,
    String,
    Error,
    console: { warn() {}, error() {} },
    alert() {},
  };
  vm.runInNewContext(compiledMain, context, { filename: mainPath });
  gameEvents.emit("cosmic-scene-ready", scene);

  const findRequests = (needle) => requests.filter((request) => request.url.includes(needle));
  const resolveRequest = (request, data, ok = true, status = 200) => {
    request.settled = true;
    request.deferred.resolve(makeResponse(data, ok, status));
  };
  const flush = async () => {
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
  };
  const runTimers = (kind, delay) => {
    const selected = [...timers.values()].filter((timer) => timer.kind === kind && timer.delay === delay);
    for (const timer of selected) {
      if (!timers.has(timer.id)) continue;
      if (kind === "timeout") timers.delete(timer.id);
      timer.callback();
    }
  };

  return {
    calls, context, document, elements, findRequests, game, interactionModes, resolveRequest, runTimers,
    scene, sockets, spacePanStates, speedButtons, timers, flush,
  };
}

const runningMessage = {
  runtime: { paused: false, active_cli: "agy", active_model: "gemini-3.8-flash-high" },
  scene: { entities: [{ id: "node_1", label: "星靈一號", type: "agent", state: "awake", monologue: "hello" }], links: [], monologues: [] },
};

test("a paused first WebSocket message closes the socket without starting requests", () => {
  const app = makeHarness();
  const socket = app.sockets[0];
  socket.deliver({ runtime: { paused: true } });

  assert.equal(socket.closeCalls.length, 1);
  assert.equal(socket.closeCalls[0].reason, "觀察者暫停");
  assert.equal(app.calls.length, 0);
  assert.equal(app.timers.size, 0);
});

test("evolution status follows scheduler state independently of the scene epoch", () => {
  const app = makeHarness();
  const status = app.elements.get("evolution-status");
  const socket = app.sockets[0];

  socket.deliver({ runtime: { paused: false, scheduler_status: { status: "running" } } });
  assert.equal(status.textContent, "AI 思考中");
  socket.deliver({ runtime: { paused: false, scheduler_status: { status: "deployed" } } });
  assert.equal(status.textContent, "部署成功");
  socket.deliver({ runtime: { paused: false, scheduler_status: { status: "sleeping" } } });
  assert.equal(status.textContent, "休眠中");
  socket.deliver({ runtime: { paused: false, scheduler_status: { status: "error", error: "permission denied\ntrace" } } });
  assert.equal(status.textContent, "未完成：permission denied trace");
  assert.equal(status.title, "permission denied trace");
  socket.deliver({ runtime: { paused: true, scheduler_status: { status: "running" } } });
  assert.equal(status.textContent, "已暫停");
});

test("the observer surfaces optional self-authored epoch and reflection fields", () => {
  const app = makeHarness();
  app.sockets[0].deliver({
    ...runningMessage,
    scene: {
      ...runningMessage.scene,
      metrics: {
        epoch: 8,
        epoch_name: "初次回聲紀",
        mental_state: "好奇",
        current_intent: "理解新生群落的第一個選擇",
        recent_reflection: "連結也可能改變彼此。",
      },
    },
  });

  assert.equal(app.elements.get("cosmic-epoch-name").textContent, "初次回聲紀");
  assert.equal(app.elements.get("cosmic-mood").textContent, "好奇");
  assert.equal(app.elements.get("cosmic-mood").classList.contains("awakened"), true);
  assert.equal(app.elements.get("cosmic-intent").textContent, "理解新生群落的第一個選擇");
  assert.equal(app.elements.get("cosmic-reflection").textContent, "連結也可能改變彼此。");
});

test("a late model-list response cannot overwrite a model being selected", async () => {
  const app = makeHarness();
  const socket = app.sockets[0];
  socket.deliver(runningMessage);

  const modelListRequest = app.findRequests("/api/control/models")[0];
  const modelSelect = app.elements.get("model-select");
  modelSelect.value = "gemini-3.7-flash-high";
  modelSelect.dispatch("change");
  const switchRequest = app.findRequests("/api/control/model")[0];
  assert.ok(switchRequest);

  app.resolveRequest(modelListRequest, {
    active_cli: "agy",
    active_model: "gemini-3.6-flash-medium",
    models: { agy: ["gemini-3.6-flash-medium", "gemini-3.7-flash-high"] },
  });
  await app.flush();
  assert.equal(modelSelect.value, "gemini-3.7-flash-high");

  app.resolveRequest(switchRequest, {
    status: "success",
    active_model: "gemini-3.7-flash-high",
  });
  await app.flush();
  assert.equal(modelSelect.value, "gemini-3.7-flash-high");
});

test("the pan tool toggles back to entity selection on the second click", () => {
  const app = makeHarness();
  const panButton = app.elements.get("pan-btn");

  panButton.dispatch("click");
  assert.deepEqual(app.interactionModes, ["pan"]);
  assert.equal(panButton.classList.contains("active"), true);
  assert.equal(panButton.getAttribute("aria-pressed"), "true");

  panButton.dispatch("click");
  assert.deepEqual(app.interactionModes, ["pan", "select"]);
  assert.equal(panButton.classList.contains("active"), false);
  assert.equal(panButton.getAttribute("aria-pressed"), "false");
});

test("holding space enables canvas dragging even without Phaser keyboard focus", () => {
  const app = makeHarness();
  const canvas = app.elements.get("canvas-container");
  const panButton = app.elements.get("pan-btn");

  app.document.dispatch("keydown", { code: "Space", key: " " });
  assert.equal(canvas.classList.contains("space-pan"), true);
  assert.equal(panButton.classList.contains("active"), true);
  assert.equal(app.spacePanStates.at(-1), true);

  app.document.dispatch("keyup", { code: "Space", key: " " });
  assert.equal(canvas.classList.contains("space-pan"), false);
  assert.equal(panButton.classList.contains("active"), false);
  assert.equal(app.spacePanStates.at(-1), false);
});

test("the timeline renders the current epoch and major milestones separately", async () => {
  const app = makeHarness();
  app.sockets[0].deliver(runningMessage);
  const historyRequest = app.findRequests("/api/habitat/history")[0];

  app.resolveRequest(historyRequest, {
    current_epoch: 321,
    civilization_stage: "共生紀",
    milestones: [{
      id: "major",
      type: "epoch_transition",
      message: "Aether-08 開啟新紀元",
      importance: 9,
      timestamp: 2,
      epoch: 321,
      entity_ids: ["Aether-08"],
    }],
    events: [{
      id: "minor",
      type: "consciousness",
      message: "日常心聲",
      importance: 3,
      timestamp: 1,
      epoch: 320,
      entity_ids: [],
    }],
  });
  await app.flush();

  assert.equal(app.elements.get("timeline-meta").textContent, "目前紀元 321 · 共生紀");
  assert.equal(app.elements.get("milestones-list").children.length, 1);
  assert.equal(app.elements.get("milestones-list").children[0].children[0].textContent, "紀元 321");
  assert.equal(app.elements.get("events-list").children.length, 1);
});

test("the timeline puts newer events at the leading edge", async () => {
  const app = makeHarness();
  app.sockets[0].deliver(runningMessage);
  const historyRequest = app.findRequests("/api/habitat/history")[0];

  app.resolveRequest(historyRequest, {
    current_epoch: 2,
    milestones: [],
    events: [
      { id: "old", type: "thought", message: "較早事件", timestamp: 1, epoch: 1, entity_ids: [] },
      { id: "new", type: "thought", message: "最新事件", timestamp: 2, epoch: 2, entity_ids: [] },
    ],
  });
  await app.flush();

  const events = app.elements.get("events-list").children;
  assert.equal(events.length, 2);
  assert.equal(events[0].children[0].textContent, "紀元 2");
  assert.equal(events[1].children[0].textContent, "紀元 1");
});

test("the sidebar surfaces recorded multi-entity interactions", async () => {
  const app = makeHarness();
  app.sockets[0].deliver(runningMessage);
  const historyRequest = app.findRequests("/api/habitat/history")[0];

  app.resolveRequest(historyRequest, {
    current_epoch: 12,
    milestones: [],
    events: [
      {
        id: "link",
        type: "link_formed",
        message: "Aether-20 與 Bio-Sp-10 構築連結",
        timestamp: 12,
        epoch: 12,
        entity_ids: ["Aether-20", "Bio-Sp-10"],
      },
      {
        id: "thought",
        type: "consciousness",
        message: "一段獨白",
        timestamp: 11,
        epoch: 11,
        entity_ids: ["Aether-20"],
      },
    ],
  });
  await app.flush();

  const activity = app.elements.get("interaction-activity-list").children;
  assert.equal(activity.length, 1);
  assert.equal(activity[0].children[0].textContent, "紀元 12");
});

test("selecting an entity shows its biography and recorded achievements", async () => {
  const app = makeHarness();
  app.sockets[0].deliver(runningMessage);
  const initialHistory = app.findRequests("/api/habitat/history")[0];
  app.resolveRequest(initialHistory, { current_epoch: 1, milestones: [], events: [] });
  await app.flush();

  app.scene.events.emit("entity-selected", { id: "node_1" });
  const entityHistory = app.findRequests("/api/habitat/history").at(-1);
  app.resolveRequest(entityHistory, {
    current_epoch: 2,
    milestones: [],
    events: [{
      id: "achievement",
      type: "link_formed",
      message: "node_1 與 node_2 構築連結",
      importance: 6,
      timestamp: 2,
      epoch: 2,
      entity_ids: ["node_1", "node_2"],
    }],
  });
  await app.flush();

  assert.equal(app.elements.get("entity-biography").hidden, false);
  assert.equal(app.elements.get("biography-name").textContent, "星靈一號");
  assert.equal(app.elements.get("biography-achievements").children.length, 1);
});

test("only sentient agents enable the dialogue controls", () => {
  const app = makeHarness();
  app.sockets[0].deliver(runningMessage);

  app.scene.events.emit("entity-selected", { id: "node_1" });
  assert.equal(app.elements.get("oracle-input").disabled, false);
  assert.equal(app.elements.get("dialogue-status").textContent, "意識已連線");

  app.scene.events.emit("entity-selected", { id: "Relic-1" });
  assert.equal(app.elements.get("oracle-input").disabled, true);
  assert.equal(app.elements.get("dialogue-status").textContent, "請選擇意識體");
});

test("pause aborts live requests and stops polling, refresh, reconnect, and entity-triggered history", async () => {
  const app = makeHarness();
  const socket = app.sockets[0];
  socket.deliver(runningMessage);
  const modelsRequest = app.findRequests("/api/control/models")[0];
  const initialHistory = app.findRequests("/api/habitat/history")[0];
  assert.ok(modelsRequest);
  assert.ok(initialHistory);
  assert.equal(modelsRequest.signal, initialHistory.signal);
  assert.equal(modelsRequest.signal.aborted, false);
  assert.equal([...app.timers.values()].filter((timer) => timer.kind === "interval").length, 1);

  app.resolveRequest(initialHistory, { events: [] });
  await app.flush();
  app.scene.events.emit("entity-selected", { id: "node_1" });
  const entityHistory = app.findRequests("/api/habitat/history").at(-1);
  assert.match(entityHistory.url, /entity_id=node_1/);
  app.resolveRequest(entityHistory, { events: [] });
  await app.flush();

  app.elements.get("oracle-input").value = "hello";
  app.elements.get("oracle-send-btn").dispatch("click");
  const signalRequest = app.findRequests("/api/observer/signal")[0];
  assert.ok(signalRequest);
  app.resolveRequest(signalRequest, {});
  await app.flush();
  assert.ok([...app.timers.values()].some((timer) => timer.kind === "timeout" && timer.delay === 600));

  socket.onclose?.({ reason: "連線中斷" });
  assert.ok([...app.timers.values()].some((timer) => timer.kind === "timeout" && timer.delay === 3000));
  // 先觸發重新連線計時器，再驗證暫停會關閉新連線並清除計時器。
  app.runTimers("timeout", 3000);
  const activeSocket = app.sockets.at(-1);
  activeSocket.deliver(runningMessage);
  await app.flush();
  const oldMessageHandler = activeSocket.onmessage;
  const oldPoll = [...app.timers.values()].find((timer) => timer.kind === "interval" && timer.delay === 5000);
  const startIndex = app.calls.length;

  app.elements.get("pause-btn").dispatch("click");
  const pauseRequest = app.calls.at(-1);
  assert.equal(pauseRequest.url, "/api/control/pause");
  assert.deepEqual(JSON.parse(pauseRequest.init.body), { paused: true });
  assert.equal(activeSocket.closeCalls.length, 1);
  assert.equal(modelsRequest.signal.aborted, true);
  assert.equal(initialHistory.signal.aborted, true);
  assert.equal(entityHistory.signal.aborted, true);
  assert.equal(signalRequest.signal.aborted, true);
  assert.deepEqual(app.calls.slice(startIndex).map((request) => request.url), ["/api/control/pause"]);
  assert.deepEqual([...app.timers.values()].map((timer) => [timer.kind, timer.delay]), [["timeout", 15000]]);

  oldPoll?.callback();
  oldMessageHandler?.({ data: JSON.stringify(runningMessage) });
  app.scene.events.emit("entity-selected", { id: "node_1" });
  assert.deepEqual(app.calls.slice(startIndex).map((request) => request.url), ["/api/control/pause"]);

  pauseRequest.deferred.reject(new Error("server unavailable"));
  await app.flush();
  assert.equal(app.elements.get("pause-btn").textContent, "重試暫停");
  assert.equal(app.sockets.length, 2);
  assert.deepEqual([...app.timers.values()], []);

  app.elements.get("pause-btn").dispatch("click");
  const retryRequest = app.calls.at(-1);
  assert.equal(retryRequest.url, "/api/control/pause");
  assert.deepEqual(JSON.parse(retryRequest.init.body), { paused: true });
  retryRequest.deferred.resolve(makeResponse({ paused: true }));
  await app.flush();
  assert.equal(app.elements.get("pause-btn").textContent, "恢復");
  assert.equal(app.sockets.length, 2);

  app.elements.get("pause-btn").dispatch("click");
  const resumeRequest = app.calls.at(-1);
  assert.deepEqual(JSON.parse(resumeRequest.init.body), { paused: false });
  resumeRequest.deferred.resolve(makeResponse({ paused: false }));
  await app.flush();
  assert.equal(app.sockets.length, 3);
  assert.equal(app.elements.get("pause-btn").textContent, "暫停");
  assert.equal(app.elements.get("model-select").disabled, false);
  assert.equal(app.findRequests("/api/control/models").length, 2);
  assert.equal(app.findRequests("/api/habitat/history").length, 3);

  oldMessageHandler?.({ data: JSON.stringify(runningMessage) });
  assert.equal(app.findRequests("/api/control/models").length, 2);
  assert.equal(app.findRequests("/api/habitat/history").length, 3);
  const resumedSocket = app.sockets.at(-1);
  resumedSocket.deliver(runningMessage);
  assert.equal(app.findRequests("/api/control/models").length, 2);
  assert.equal(app.findRequests("/api/habitat/history").length, 3);
  const resumedRequests = app.calls.slice(-2);
  assert.equal(resumedRequests[0].signal, resumedRequests[1].signal);
  assert.equal(resumedRequests[0].signal.aborted, false);
  assert.equal([...app.timers.values()].filter((timer) => timer.kind === "interval" && timer.delay === 5000).length, 1);
});

test("pausing while reconnect is scheduled clears reconnect and poll timers", async () => {
  const app = makeHarness();
  const socket = app.sockets[0];
  socket.deliver(runningMessage);
  const modelsRequest = app.findRequests("/api/control/models")[0];
  const historyRequest = app.findRequests("/api/habitat/history")[0];
  app.resolveRequest(modelsRequest, { models: {}, active_cli: "agy" });
  app.resolveRequest(historyRequest, { events: [] });
  await app.flush();

  socket.onclose?.({ reason: "連線中斷" });
  assert.deepEqual([...app.timers.values()].map((timer) => timer.delay).sort((a, b) => a - b), [3000, 5000]);
  app.elements.get("pause-btn").dispatch("click");
  const pauseRequest = app.calls.at(-1);
  assert.equal(pauseRequest.url, "/api/control/pause");
  assert.equal(modelsRequest.signal.aborted, true);
  assert.equal(historyRequest.signal.aborted, true);
  assert.deepEqual([...app.timers.values()].map((timer) => timer.delay), [15000]);
  pauseRequest.deferred.resolve(makeResponse({ paused: true }));
  await app.flush();
  assert.deepEqual([...app.timers.values()], []);
  assert.equal(app.sockets.length, 1);
});

test("late history completion and a failed resume leave the observer disconnected", async () => {
  const app = makeHarness();
  const socket = app.sockets[0];
  socket.deliver(runningMessage);
  const modelsRequest = app.findRequests("/api/control/models")[0];
  const historyRequest = app.findRequests("/api/habitat/history")[0];
  const oldMessageHandler = socket.onmessage;

  app.elements.get("pause-btn").dispatch("click");
  const pauseRequest = app.calls.at(-1);
  assert.equal(pauseRequest.url, "/api/control/pause");
  assert.equal(modelsRequest.signal.aborted, true);
  assert.equal(historyRequest.signal.aborted, true);
  assert.equal(app.calls.filter((request) => request.url.startsWith("/api/")).length, 3);

  // 確認取消後才抵達的回應不會更新畫面或重新啟動網路請求。
  app.resolveRequest(historyRequest, { events: [{ type: "late", message: "ignored", timestamp: 1 }] });
  oldMessageHandler?.({ data: JSON.stringify(runningMessage) });
  assert.equal(app.calls.length, 3);
  pauseRequest.deferred.resolve(makeResponse({ paused: true }));
  await app.flush();

  app.elements.get("pause-btn").dispatch("click");
  const resumeRequest = app.calls.at(-1);
  assert.equal(resumeRequest.url, "/api/control/pause");
  assert.deepEqual(JSON.parse(resumeRequest.init.body), { paused: false });
  resumeRequest.deferred.reject(new Error("resume failed"));
  await app.flush();

  assert.equal(app.sockets.length, 1);
  assert.equal(socket.closeCalls.length, 1);
  assert.deepEqual([...app.timers.values()], []);
  assert.equal(app.calls.filter((request) => request.url.startsWith("/api/")).length, 4);
  assert.equal(app.elements.get("pause-btn").textContent, "恢復");

  app.elements.get("pause-btn").dispatch("click");
  assert.deepEqual(JSON.parse(app.calls.at(-1).init.body), { paused: false });
});
