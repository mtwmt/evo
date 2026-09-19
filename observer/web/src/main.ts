import Phaser from "phaser";
import { CanvasInteractionMode, CosmicScene } from "./scenes/CosmicScene";

// 初始化 Phaser 遊戲實例
const config: Phaser.Types.Core.GameConfig = {
  type: Phaser.AUTO,
  parent: "canvas-container",
  width: 800,
  height: 600,
  backgroundColor: "#0b0e14",
  scene: [CosmicScene],
};

const game = new Phaser.Game(config);

// DOM 元素引用
const cliSelect = document.getElementById("cli-select") as HTMLSelectElement;
const modelSelect = document.getElementById("model-select") as HTMLSelectElement;
const codexEffortControl = document.getElementById("codex-effort-control") as HTMLElement;
const codexEffortSelect = document.getElementById("codex-effort-select") as HTMLSelectElement;
const claudeEffortControl = document.getElementById("claude-effort-control") as HTMLElement;
const claudeEffortSelect = document.getElementById("claude-effort-select") as HTMLSelectElement;
const sidebar = document.getElementById("sidebar") as HTMLElement;
const sidebarResizer = document.getElementById("sidebar-resizer") as HTMLElement;
const canvasContainer = document.getElementById("canvas-container") as HTMLElement;
const zoomInBtn = document.getElementById("zoom-in-btn") as HTMLButtonElement;
const zoomOutBtn = document.getElementById("zoom-out-btn") as HTMLButtonElement;
const zoomResetBtn = document.getElementById("zoom-reset-btn") as HTMLButtonElement;
const panBtn = document.getElementById("pan-btn") as HTMLButtonElement;
const speedBtns = document.querySelectorAll(".speed-btn");
const pauseBtn = document.getElementById("pause-btn") as HTMLButtonElement;
const statCpu = document.getElementById("stat-cpu") as HTMLElement;
const statRam = document.getElementById("stat-ram") as HTMLElement;
const statEpoch = document.getElementById("stat-epoch") as HTMLElement;
const eventsList = document.getElementById("events-list") as HTMLElement;
const historyScope = document.getElementById("history-scope") as HTMLElement;
const oracleInput = document.getElementById("oracle-input") as HTMLInputElement;
const oracleSendBtn = document.getElementById("oracle-send-btn") as HTMLButtonElement;
const oracleFeedback = document.getElementById("oracle-feedback") as HTMLElement;
const dialogueLog = document.getElementById("dialogue-log") as HTMLElement;

// 獨白面板元素引用
const selectedEntityBadge = document.getElementById("selected-entity-badge") as HTMLElement;
const speakerId = document.getElementById("speaker-id") as HTMLElement;
const speakerState = document.getElementById("speaker-state") as HTMLElement;
const speakerThought = document.getElementById("speaker-thought") as HTMLElement;
const speakerIndicator = document.getElementById("speaker-indicator") as HTMLElement;
const monologueStreamList = document.getElementById("monologue-stream-list") as HTMLElement;
const interactionList = document.getElementById("interaction-list") as HTMLElement;
const interactionCount = document.getElementById("interaction-count") as HTMLElement;

let isPaused = false;
let selectedEntityId: string | null = null;
let latestSceneData: any = null;
let cosmicScene: CosmicScene | null = null;
let lastEntityMap = new Map<string, any>();
let isModelChangePending = false;
let availableModelsMap: Record<string, string[]> = {
  // Agy 的模型探索需要啟動 CLI；在它暫時不可用時仍完整保留帳號可選模型。
  // 正常情況會由 /api/control/models 的 `agy models` 即時結果覆蓋此清單。
  agy: [
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
  ],
  codex: ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
  claude: ["fable", "opus", "sonnet", "haiku"],
};

function renderCanvasTool(mode: CanvasInteractionMode): void {
  panBtn.classList.remove("active");
  canvasContainer.classList.remove("mode-pan");
  if (mode === "pan") panBtn.classList.add("active");
  if (mode === "pan") canvasContainer.classList.add("mode-pan");
}

function setCanvasTool(mode: CanvasInteractionMode): void {
  cosmicScene?.setInteractionMode(mode);
  renderCanvasTool(mode);
}

// 右側觀測欄可依內容自由調整寬度。
const SIDEBAR_MIN_WIDTH = 320;
const SIDEBAR_MAX_WIDTH = 720;

sidebarResizer.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  sidebarResizer.setPointerCapture(event.pointerId);
  sidebarResizer.classList.add("dragging");
  document.body.classList.add("sidebar-resizing");
});

sidebarResizer.addEventListener("pointermove", (event) => {
  if (!sidebarResizer.hasPointerCapture(event.pointerId)) return;
  const maxWidth = Math.min(SIDEBAR_MAX_WIDTH, window.innerWidth - SIDEBAR_MIN_WIDTH);
  const nextWidth = Math.min(
    maxWidth,
    Math.max(SIDEBAR_MIN_WIDTH, window.innerWidth - event.clientX),
  );
  sidebar.style.width = `${nextWidth}px`;
});

function finishSidebarResize(event: PointerEvent) {
  if (sidebarResizer.hasPointerCapture(event.pointerId)) {
    sidebarResizer.releasePointerCapture(event.pointerId);
  }
  sidebarResizer.classList.remove("dragging");
  document.body.classList.remove("sidebar-resizing");
}

sidebarResizer.addEventListener("pointerup", finishSidebarResize);
sidebarResizer.addEventListener("pointercancel", finishSidebarResize);

// 更新模型下拉選單
function updateModelDropdown(cliName: string, activeModel?: string) {
  codexEffortControl.hidden = cliName !== "codex";
  claudeEffortControl.hidden = cliName !== "claude";
  modelSelect.innerHTML = "";
  const models = availableModelsMap[cliName] || [];
  models.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    if (activeModel && activeModel === m) {
      opt.selected = true;
    }
    modelSelect.appendChild(opt);
  });

  // 加入「自訂模型...」選項
  const customOpt = document.createElement("option");
  customOpt.value = "__custom__";
  customOpt.textContent = "+ 自訂模型名稱...";
  modelSelect.appendChild(customOpt);

  if (activeModel && models.includes(activeModel)) {
    modelSelect.value = activeModel;
  } else if (activeModel) {
    // 若為自訂模型，動態插入並選取
    const customValOpt = document.createElement("option");
    customValOpt.value = activeModel;
    customValOpt.textContent = `${activeModel} (自訂)`;
    customValOpt.selected = true;
    modelSelect.insertBefore(customValOpt, customOpt);
    modelSelect.value = activeModel;
  }
}

// 初始加載當前模型清單
async function initModelsList() {
  try {
    const res = await fetch("/api/control/models");
    if (res.ok) {
      const data = await res.json();
      if (data.models) {
        availableModelsMap = Object.fromEntries(
          Object.entries(data.models).map(([cliName, models]) => [
            cliName,
            Array.isArray(models) && models.length > 0
              ? models
              : (availableModelsMap[cliName] || []),
          ]),
        );
      }
      if (data.active_cli) {
        cliSelect.value = data.active_cli;
        updateModelDropdown(data.active_cli, data.active_model);
      }
      if (data.codex_effort) codexEffortSelect.value = data.codex_effort;
      if (data.claude_effort) claudeEffortSelect.value = data.claude_effort;
    }
  } catch (err) {
    updateModelDropdown(cliSelect.value);
  }
}
initModelsList();

// === 1. 綁定 Phaser 場景實體點擊事件 ===
game.events.once("cosmic-scene-ready", (scene: CosmicScene) => {
  cosmicScene = scene;
  scene.events.on("interaction-mode-changed", (mode: CanvasInteractionMode) => renderCanvasTool(mode));
  scene.events.on("entity-selected", (entity: any) => {
    selectEntity(entity.id);
  });

  scene.events.on("entity-deselected", () => {
    selectedEntityId = null;
    selectedEntityBadge.textContent = "全域意識漫遊";
    selectedEntityBadge.classList.remove("locked");
    oracleInput.placeholder = "輸入訊號，與宇宙對話…";
    updateInteractions(latestSceneData);
    fetchHistory();
  });
});

function selectEntity(entityId: string) {
  const entity = lastEntityMap.get(entityId);
  if (!entity) return;
  selectedEntityId = entityId;
  if (cosmicScene) cosmicScene.selectedEntityId = entityId;
  selectedEntityBadge.textContent = `鎖定目標：${entityId}`;
  selectedEntityBadge.classList.add("locked");
  updateMonologueDisplay(entity);
  updateInteractions(latestSceneData);
  fetchHistory();
}

zoomInBtn.addEventListener("click", () => cosmicScene?.zoomBy(0.15));
zoomOutBtn.addEventListener("click", () => cosmicScene?.zoomBy(-0.15));
panBtn.addEventListener("click", () => setCanvasTool("pan"));
zoomResetBtn.addEventListener("click", () => {
  cosmicScene?.resetZoom();
  setCanvasTool("select");
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setCanvasTool("select");
});

function updateMonologueDisplay(entity: any) {
  if (!entity) return;
  speakerId.textContent = entity.id;
  speakerState.textContent = entity.state || "感應中";
  speakerThought.textContent = `「${entity.monologue || "意識正在虛空中凝聚..."}」`;
  if (entity.color) {
    speakerIndicator.style.backgroundColor = entity.color;
    speakerIndicator.style.boxShadow = `0 0 8px ${entity.color}`;
  }
  oracleInput.placeholder = `對 ${entity.id} 說些什麼…`;
}

function updateInteractions(scene: any) {
  latestSceneData = scene;
  const allLinks = Array.isArray(scene?.links) ? scene.links : [];
  const links = selectedEntityId
    ? allLinks.filter((link: any) => link.from === selectedEntityId || link.to === selectedEntityId)
    : allLinks;
  interactionCount.textContent = selectedEntityId
    ? `${selectedEntityId} · ${links.length} 條`
    : `全域 · ${links.length} 條`;
  interactionList.innerHTML = "";

  if (links.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-interaction";
    empty.textContent = selectedEntityId
      ? `${selectedEntityId} 此刻尚未形成可觀測的連結`
      : "此刻尚未形成可觀測的連結";
    interactionList.appendChild(empty);
    return;
  }

  links
    .slice()
    .sort((a: any, b: any) => (b.width || 0) - (a.width || 0))
    .forEach((link: any) => {
      const item = document.createElement("li");
      const pair = document.createElement("strong");
      pair.textContent = `${link.from} ↔ ${link.to}`;
      const detail = document.createElement("span");
      detail.textContent = "能量連結";
      item.append(pair, detail);
      interactionList.appendChild(item);
    });
}

// === 2. WebSocket 宇宙串流監聽 ===
function initWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.port === "5173" ? "127.0.0.1:8000" : window.location.host;
  const wsUrl = `${protocol}//${host}/ws/universe`;

  const ws = new WebSocket(wsUrl);

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      const scene = game.scene.getScene("CosmicScene") as CosmicScene;
      if (scene && data.scene) {
        scene.updateScene(data.scene);
        updateInteractions(data.scene);
      }

      // 追蹤所有實體當前狀態
      if (Array.isArray(data.scene?.entities)) {
        lastEntityMap.clear();
        data.scene.entities.forEach((e: any) => lastEntityMap.set(e.id, e));

        // 若已鎖定特定實體，持續同步其第一人稱心聲
        if (selectedEntityId && lastEntityMap.has(selectedEntityId)) {
          const lockedEntity = lastEntityMap.get(selectedEntityId);
          updateMonologueDisplay(lockedEntity);
        } else if (!selectedEntityId && data.scene.entities.length > 0) {
          // 未手動鎖定時，定期輪播代表性心聲
          const randomNode = data.scene.entities[Math.floor(Math.random() * data.scene.entities.length)];
          if (Math.random() < 0.25) {
            updateMonologueDisplay(randomNode);
          }
        }
      }

      // 更新即時意識廣播列表
      if (Array.isArray(data.scene?.monologues) && monologueStreamList) {
        monologueStreamList.innerHTML = "";
        data.scene.monologues.forEach((m: any) => {
          const li = document.createElement("li");
          const id = document.createElement("strong");
          id.textContent = String(m.id);
          const state = document.createElement("span");
          state.className = "state";
          state.textContent = ` [${m.state}] `;
          const thought = document.createElement("em");
          thought.textContent = `: ${m.monologue}`;
          li.append(id, state, thought);
          li.addEventListener("click", () => {
            const ent = lastEntityMap.get(m.id);
            if (ent && scene) {
              selectEntity(m.id);
            }
          });
          monologueStreamList.appendChild(li);
        });
      }

      // 更新遙測指標
      if (data.resources) {
        statCpu.textContent = `CPU: ${data.resources.cpu_percent}%`;
        statRam.textContent = `RAM: ${data.resources.ram_mb} MB`;
      }
      if (data.scene?.metrics) {
        statEpoch.textContent = `紀元: ${data.scene.metrics.epoch || 0}`;
      }

      // 同步 CLI、Model 與速度設定狀態
      if (data.runtime) {
        const activeModelIsAvailable = Array.from(modelSelect.options).some(
          (option) => option.value === data.runtime.active_model,
        );
        const isChoosingModel = document.activeElement === modelSelect || isModelChangePending;
        if (!isChoosingModel) {
          if (cliSelect.value !== data.runtime.active_cli || !activeModelIsAvailable) {
            cliSelect.value = data.runtime.active_cli;
            updateModelDropdown(data.runtime.active_cli, data.runtime.active_model);
          } else if (modelSelect.value !== data.runtime.active_model) {
            modelSelect.value = data.runtime.active_model;
          }
        }
        if (data.runtime.codex_effort) codexEffortSelect.value = data.runtime.codex_effort;
        if (data.runtime.claude_effort) claudeEffortSelect.value = data.runtime.claude_effort;

        isPaused = data.runtime.paused;
        pauseBtn.textContent = isPaused ? "恢復" : "暫停";
      }
    } catch (err) {
      console.error("[WebSocket] 解析訊息失敗：", err);
    }
  };

  ws.onclose = () => {
    console.warn("[WebSocket] 連線中斷，3 秒後嘗試重新連線...");
    setTimeout(initWebSocket, 3000);
  };
}

initWebSocket();

// === 3. 歷史事件定時拉取 ===
async function fetchHistory() {
  try {
    const params = new URLSearchParams({ limit: "15" });
    if (selectedEntityId) params.set("entity_id", selectedEntityId);
    const res = await fetch(`/api/habitat/history?${params}`);
    if (res.ok) {
      const data = await res.json();
      eventsList.innerHTML = "";
      historyScope.textContent = selectedEntityId ? `${selectedEntityId} 的歷程` : "全域事件";
      if (Array.isArray(data.events)) {
        dialogueLog.hidden = !selectedEntityId;
        dialogueLog.innerHTML = "";
        if (selectedEntityId) {
          data.events
            .filter((ev: any) => ev.type === "dialogue_request" || ev.type === "dialogue_response")
            .reverse()
            .forEach((ev: any) => {
              const line = document.createElement("p");
              line.className = `dialogue-line ${ev.type === "dialogue_response" ? "response" : "request"}`;
              line.textContent = ev.type === "dialogue_response"
                ? `${selectedEntityId}：${String(ev.message).replace(/^\[[^\]]+\] 回應：/, "")}`
                : `你：${String(ev.message).replace(/^\[[^\]]+\] 收到觀測者訊號：/, "")}`;
              dialogueLog.appendChild(line);
            });
        }
        data.events.forEach((ev: any) => {
          const li = document.createElement("li");
          const timestamp = new Date(ev.timestamp * 1000);
          const time = Number.isNaN(timestamp.getTime())
            ? ""
            : timestamp.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" });
          const timeElement = document.createElement("time");
          timeElement.textContent = time;
          const message = document.createElement("span");
          message.append(document.createTextNode(`[${ev.type}] `));
          String(ev.message).split(/(node_\d+)/g).forEach((part) => {
            if (/^node_\d+$/.test(part)) {
              const entityLink = document.createElement("button");
              entityLink.type = "button";
              entityLink.className = "history-inline-node";
              entityLink.textContent = part;
              entityLink.addEventListener("click", () => selectEntity(part));
              message.appendChild(entityLink);
            } else {
              message.append(document.createTextNode(part));
            }
          });
          li.append(timeElement, message);
          eventsList.appendChild(li);
        });
      }
    }
  } catch (err) {
    // 忽略未連線錯誤
  }
}
setInterval(fetchHistory, 5000);
fetchHistory();

// === 4. 控制面板事件綁定 ===

// 切換 CLI 適配器
cliSelect.addEventListener("change", async () => {
  const targetCli = cliSelect.value;
  try {
    const res = await fetch("/api/control/cli", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cli: targetCli }),
    });
    if (res.ok) {
      const data = await res.json();
      updateModelDropdown(targetCli, data.active_model);
    } else {
      const errData = await res.json();
      alert(`切換 CLI 失敗：${errData.detail || "未知錯誤"}`);
      // 還原選單狀態
      initModelsList();
    }
  } catch (err) {
    alert("與後端通訊失敗");
  }
});

// 切換 Model 模型
modelSelect.addEventListener("change", async () => {
  let targetModel = modelSelect.value;
  if (targetModel === "__custom__") {
    const customName = window.prompt("請輸入自訂模型名稱（例如：gpt-6-astra、claude-fable-5-1、gemini-3.8-pro 等）：");
    if (!customName || !customName.trim()) {
      // 使用者取消輸入，重新重整選單
      initModelsList();
      return;
    }
    targetModel = customName.trim();
  }

  isModelChangePending = true;
  try {
    const res = await fetch("/api/control/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: targetModel }),
    });
    if (res.ok) {
      updateModelDropdown(cliSelect.value, targetModel);
    } else {
      const errData = await res.json();
      alert(`切換 Model 失敗：${errData.detail || "未知錯誤"}`);
      initModelsList();
    }
  } catch (err) {
    alert("與後端通訊失敗");
  } finally {
    isModelChangePending = false;
  }
});

async function setEffort(cli: string, effort: string) {
  try {
    const res = await fetch("/api/control/effort", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cli, effort }),
    });
    if (!res.ok) {
      const errData = await res.json();
      alert(`設定思考速度失敗：${errData.detail || "未知錯誤"}`);
      initModelsList();
    }
  } catch (err) {
    alert("與後端通訊失敗");
    initModelsList();
  }
}

codexEffortSelect.addEventListener("change", () => setEffort("codex", codexEffortSelect.value));
claudeEffortSelect.addEventListener("change", () => setEffort("claude", claudeEffortSelect.value));

async function sendOracleSignal() {
  const message = oracleInput.value.trim();
  if (!message) return;

  try {
    oracleFeedback.textContent = "傳送中…";
    const res = await fetch("/api/observer/signal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender: "Observer",
        message,
        target_entity_id: selectedEntityId,
      }),
    });
    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || "未知錯誤");
    }
    oracleFeedback.textContent = "訊號已送出，將在下一輪演進回應。";
    oracleInput.value = "";
    window.setTimeout(fetchHistory, 600);
  } catch (err) {
    oracleFeedback.textContent = `傳送失敗：${err instanceof Error ? err.message : "通訊異常"}`;
  }
}

oracleSendBtn.addEventListener("click", sendOracleSignal);
oracleInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") sendOracleSignal();
});

// 設定模擬速度
speedBtns.forEach((btn) => {
  btn.addEventListener("click", async () => {
    const speed = btn.getAttribute("data-speed");
    if (!speed) return;

    try {
      const res = await fetch("/api/control/speed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed }),
      });
      if (res.ok) {
        speedBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
      }
    } catch (err) {
      console.error("設定速度失敗：", err);
    }
  });
});

// 暫停／恢復
pauseBtn.addEventListener("click", async () => {
  const nextState = !isPaused;
  try {
    const res = await fetch("/api/control/pause", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused: nextState }),
    });
    if (res.ok) {
      isPaused = nextState;
      pauseBtn.textContent = isPaused ? "恢復" : "暫停";
    }
  } catch (err) {
    console.error("切換暫停狀態失敗：", err);
  }
});
