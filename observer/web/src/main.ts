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
const evolutionStatus = document.getElementById("evolution-status") as HTMLElement;
const eventsList = document.getElementById("events-list") as HTMLElement;
const milestonesList = document.getElementById("milestones-list") as HTMLElement;
const timelineMeta = document.getElementById("timeline-meta") as HTMLElement;
const historyScope = document.getElementById("history-scope") as HTMLElement;
const oracleInput = document.getElementById("oracle-input") as HTMLInputElement;
const oracleSendBtn = document.getElementById("oracle-send-btn") as HTMLButtonElement;
const oracleFeedback = document.getElementById("oracle-feedback") as HTMLElement;
const dialogueLog = document.getElementById("dialogue-log") as HTMLElement;
const dialogueTarget = document.getElementById("dialogue-target") as HTMLElement;
const dialogueStatus = document.getElementById("dialogue-status") as HTMLElement;
const biographyPanel = document.getElementById("entity-biography") as HTMLElement;
const biographyName = document.getElementById("biography-name") as HTMLElement;
const biographyKind = document.getElementById("biography-kind") as HTMLElement;
const biographyState = document.getElementById("biography-state") as HTMLElement;
const biographyEventCount = document.getElementById("biography-event-count") as HTMLElement;
const biographyAchievements = document.getElementById("biography-achievements") as HTMLElement;
const cosmicMood = document.getElementById("cosmic-mood") as HTMLElement;
const cosmicEpochName = document.getElementById("cosmic-epoch-name") as HTMLElement;
const cosmicIntent = document.getElementById("cosmic-intent") as HTMLElement;
const cosmicReflection = document.getElementById("cosmic-reflection") as HTMLElement;

// 獨白面板元素引用
const selectedEntityBadge = document.getElementById("selected-entity-badge") as HTMLElement;
const speakerId = document.getElementById("speaker-id") as HTMLElement;
const speakerState = document.getElementById("speaker-state") as HTMLElement;
const speakerThought = document.getElementById("speaker-thought") as HTMLElement;
const speakerIndicator = document.getElementById("speaker-indicator") as HTMLElement;
const monologueStreamList = document.getElementById("monologue-stream-list") as HTMLElement;
const interactionList = document.getElementById("interaction-list") as HTMLElement;
const interactionCount = document.getElementById("interaction-count") as HTMLElement;
const interactionActivityList = document.getElementById("interaction-activity-list") as HTMLElement;

let isPaused = false;
let canvasInteractionMode: CanvasInteractionMode = "select";
let isSpacePanHeld = false;
let isDialogueAvailable = false;
let selectedEntityId: string | null = null;
let latestSceneData: any = null;
let cosmicScene: CosmicScene | null = null;
let lastEntityMap = new Map<string, any>();
let isCLIChangePending = false;
let isModelChangePending = false;
let controlRevision = 0;
let modelListRequestSequence = 0;
let universeSocket: WebSocket | null = null;
let websocketReconnectTimer: number | null = null;
let historyPollTimer: number | null = null;
let historyRefreshTimer: number | null = null;
let historyRequestPending = false;
let networkController = new AbortController();
let networkReady = false;
let pauseControlPending = false;
let pauseNeedsRetry = false;

function renderEvolutionStatus(runtime: any): void {
  if (runtime?.paused || runtime?.scheduler_status?.status === "paused") {
    evolutionStatus.textContent = "已暫停";
    evolutionStatus.title = "AI 演化已暫停";
    return;
  }

  const status = runtime?.scheduler_status;
  const state = status?.status;
  const reason = status?.reason || status?.error || status?.warning || "";
  const labels: Record<string, string> = {
    idle: "AI 尚未開始",
    disconnected: "觀測已斷線",
    running: "AI 思考中",
    thinking: "AI 思考中",
    verifying: "驗證中",
    deploying: "部署中",
    deployed: "部署成功",
    sleeping: "休眠中",
    ai_sleep_requested: "休眠中",
    paused: "已暫停",
    pause_failed: "未完成：暫停失敗",
    resume_failed: "未完成：恢復失敗",
    resource_limited: "未完成：資源不足",
    error: "未完成",
    rollback: "未完成：部署已回復",
    rollback_error: "未完成：回復失敗",
    abandoned: "未完成",
    cancelled: "未完成：已取消",
    skipped: "未完成：本輪略過",
  };
  const label = labels[state] || (state ? "未完成" : "AI 尚未開始");
  const detail = typeof reason === "string" ? reason.trim().replace(/[\r\n\t ]+/g, " ") : "";
  evolutionStatus.textContent = label.startsWith("未完成") && detail
    ? `${label}：${detail.slice(0, 72)}` : label;
  evolutionStatus.title = detail || label;
}

// 暫停時共用的 signal 會中止所有請求，包含尚未讀完的回應內容。
function appFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  if (isPaused || pauseControlPending || !networkReady) {
    return Promise.reject(new DOMException("宇宙已暫停連線", "AbortError"));
  }
  return fetch(input, { ...init, signal: networkController.signal });
}

function isRequestCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function renderConnectionControls(): void {
  const disabled = isPaused || pauseControlPending || !networkReady;
  [cliSelect, modelSelect, codexEffortSelect, claudeEffortSelect]
    .forEach((control) => { control.disabled = disabled; });
  [oracleSendBtn, oracleInput]
    .forEach((control) => { control.disabled = disabled || !isDialogueAvailable; });
  speedBtns.forEach((button) => { (button as HTMLButtonElement).disabled = disabled; });
  pauseBtn.disabled = pauseControlPending;
  pauseBtn.textContent = pauseControlPending ? "處理中…"
    : pauseNeedsRetry ? "重試暫停" : isPaused ? "恢復" : "暫停";
}

function entityDisplayName(entity: any, fallbackId = ""): string {
  const label = entity?.label;
  if (typeof label === "string" && label.trim()) return label.trim();
  return fallbackId || "未命名實體";
}

function selectedEntityDisplayName(): string {
  if (!selectedEntityId) return "宇宙";
  return entityDisplayName(lastEntityMap.get(selectedEntityId), selectedEntityId);
}

function setDialogueAvailability(entity: any): void {
  const displayName = entityDisplayName(entity, entity?.id);
  isDialogueAvailable = entity?.type === "agent";
  if (isDialogueAvailable) {
    dialogueTarget.textContent = displayName;
    dialogueStatus.textContent = "意識已連線";
    dialogueStatus.classList.add("locked");
    oracleInput.placeholder = `對 ${displayName} 說些什麼…`;
  } else if (entity) {
    dialogueTarget.textContent = displayName;
    dialogueStatus.textContent = "非意識實體";
    dialogueStatus.classList.remove("locked");
    oracleInput.placeholder = "此實體沒有可對話的意識。";
  } else {
    dialogueTarget.textContent = "宇宙";
    dialogueStatus.textContent = "請選擇意識體";
    dialogueStatus.classList.remove("locked");
    oracleInput.placeholder = "請先選擇有意識的生命體…";
  }
  renderConnectionControls();
}

function disconnectRealtime(): void {
  isPaused = true;
  renderEvolutionStatus({ scheduler_status: { status: "disconnected" } });
  networkReady = false;
  networkController.abort();
  if (historyPollTimer !== null) window.clearInterval(historyPollTimer);
  if (historyRefreshTimer !== null) window.clearTimeout(historyRefreshTimer);
  if (websocketReconnectTimer !== null) window.clearTimeout(websocketReconnectTimer);
  historyPollTimer = historyRefreshTimer = websocketReconnectTimer = null;
  const socket = universeSocket;
  universeSocket = null;
  if (socket) {
    // 拔除舊連線的事件，避免延遲封包或 onclose 又重新連線。
    socket.onmessage = null;
    socket.onclose = null;
    socket.close(1000, "觀察者暫停");
  }
  renderConnectionControls();
}

function startRealtimeRequests(): void {
  if (isPaused || pauseControlPending || networkReady) return;
  networkController = new AbortController();
  networkReady = true;
  renderConnectionControls();
  void initModelsList();
  void fetchHistory();
  historyPollTimer = window.setInterval(fetchHistory, 5000);
}
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
  canvasInteractionMode = mode;
  const panIsActive = mode === "pan" || isSpacePanHeld;
  panBtn.classList.remove("active");
  canvasContainer.classList.remove("mode-pan");
  if (panIsActive) panBtn.classList.add("active");
  if (mode === "pan") canvasContainer.classList.add("mode-pan");
  panBtn.setAttribute("aria-pressed", panIsActive ? "true" : "false");
  panBtn.title = mode === "pan"
    ? "停止拖曳，返回角色選取"
    : isSpacePanHeld
      ? "按住空白鍵拖曳中"
    : "拖曳畫面（再按一次返回角色選取）";
}

function setCanvasTool(mode: CanvasInteractionMode): void {
  cosmicScene?.setInteractionMode(mode);
  renderCanvasTool(mode);
}

function setTemporaryPan(active: boolean): void {
  isSpacePanHeld = active;
  if (active) canvasContainer.classList.add("space-pan");
  else canvasContainer.classList.remove("space-pan");
  cosmicScene?.setSpacePanActive(active);
  renderCanvasTool(canvasInteractionMode);
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
  const requestSequence = ++modelListRequestSequence;
  const revisionAtStart = controlRevision;
  try {
    const res = await appFetch("/api/control/models");
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
      // 較舊的清單回應仍可補充選項，但不可覆蓋使用者剛完成的切換。
      if (
        requestSequence === modelListRequestSequence
        && revisionAtStart === controlRevision
        && !isCLIChangePending
        && !isModelChangePending
        && data.active_cli
      ) {
        cliSelect.value = data.active_cli;
        updateModelDropdown(data.active_cli, data.active_model);
      }
      if (data.codex_effort) codexEffortSelect.value = data.codex_effort;
      if (data.claude_effort) claudeEffortSelect.value = data.claude_effort;
    }
  } catch (err) {
    if (isRequestCancelled(err)) return;
    updateModelDropdown(cliSelect.value);
  }
}
renderConnectionControls();

// === 1. 綁定 Phaser 場景實體點擊事件 ===
game.events.once("cosmic-scene-ready", (scene: CosmicScene) => {
  cosmicScene = scene;
  scene.setSpacePanActive(isSpacePanHeld);
  scene.events.on("interaction-mode-changed", (mode: CanvasInteractionMode) => renderCanvasTool(mode));
  scene.events.on("entity-selected", (entity: any) => {
    selectEntity(entity.id);
  });

  scene.events.on("entity-deselected", () => {
    selectedEntityId = null;
    selectedEntityBadge.textContent = "全域意識漫遊";
    selectedEntityBadge.classList.remove("locked");
    biographyPanel.hidden = true;
    setDialogueAvailability(null);
    updateInteractions(latestSceneData);
    fetchHistory();
  });
});

function selectEntity(entityId: string) {
  const entity = lastEntityMap.get(entityId);
  const displayName = entityDisplayName(entity, entityId);
  selectedEntityId = entityId;
  if (cosmicScene && entity) cosmicScene.selectedEntityId = entityId;
  selectedEntityBadge.textContent = `鎖定目標：${displayName}`;
  selectedEntityBadge.classList.add("locked");
  if (entity) {
    updateMonologueDisplay(entity);
  } else {
    speakerId.textContent = displayName;
    speakerState.textContent = "歷史紀錄";
    speakerThought.textContent = "「此實體目前不在畫布中；正在讀取已留下的事蹟。」";
  }
  setDialogueAvailability(entity);
  renderEntityBiography(entity, []);
  updateInteractions(latestSceneData);
  fetchHistory();
}

zoomInBtn.addEventListener("click", () => cosmicScene?.zoomBy(0.15));
zoomOutBtn.addEventListener("click", () => cosmicScene?.zoomBy(-0.15));
panBtn.addEventListener("click", () => {
  setCanvasTool(canvasInteractionMode === "pan" ? "select" : "pan");
});
zoomResetBtn.addEventListener("click", () => {
  cosmicScene?.resetZoom();
  setCanvasTool("select");
});

function isTextEntryTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return Boolean(
    element?.isContentEditable
    || ["INPUT", "TEXTAREA", "SELECT"].includes(element?.tagName || ""),
  );
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setCanvasTool("select");
  if ((event.code === "Space" || event.key === " ") && !isTextEntryTarget(event.target)) {
    setTemporaryPan(true);
    event.preventDefault();
  }
});
document.addEventListener("keyup", (event) => {
  if (event.code === "Space" || event.key === " ") {
    setTemporaryPan(false);
  }
});
window.addEventListener("blur", () => setTemporaryPan(false));

function updateMonologueDisplay(entity: any) {
  if (!entity) return;
  const displayName = entityDisplayName(entity, entity.id);
  speakerId.textContent = displayName;
  speakerState.textContent = entity.state || "感應中";
  speakerThought.textContent = `「${entity.monologue || "意識正在虛空中凝聚..."}」`;
  if (entity.color) {
    speakerIndicator.style.backgroundColor = entity.color;
    speakerIndicator.style.boxShadow = `0 0 8px ${entity.color}`;
  }
  oracleInput.placeholder = `對 ${displayName} 說些什麼…`;
}

function firstPublicText(metrics: any, keys: string[]): string {
  if (!metrics || typeof metrics !== "object") return "";
  for (const key of keys) {
    const value = metrics[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function updateCosmicReflection(metrics: any): void {
  const epochName = firstPublicText(metrics, ["epoch_name", "era_name", "civilization_stage"]);
  const mood = firstPublicText(metrics, ["mood", "mental_state", "state_of_mind"]);
  const intent = firstPublicText(metrics, ["intent", "current_intent", "purpose"]);
  const reflection = firstPublicText(metrics, ["reflection", "recent_reflection", "world_thought"]);

  cosmicEpochName.textContent = epochName || "宇宙尚未為自己命名";
  cosmicMood.textContent = mood || "尚未命名";
  cosmicMood.classList.toggle("awakened", Boolean(mood));
  cosmicIntent.textContent = intent || "正在觀察自己如何成形…";
  cosmicReflection.textContent = reflection || "尚未留下可公開的內在記錄。";
}

function updateInteractions(scene: any) {
  latestSceneData = scene;
  const allLinks = Array.isArray(scene?.links) ? scene.links : [];
  const links = selectedEntityId
    ? allLinks.filter((link: any) => link.from === selectedEntityId || link.to === selectedEntityId)
    : allLinks;
  const selectedName = selectedEntityDisplayName();
  interactionCount.textContent = selectedEntityId
    ? `${selectedName} · ${links.length} 條`
    : `全域 · ${links.length} 條`;
  interactionList.innerHTML = "";

  if (links.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-interaction";
    empty.textContent = selectedEntityId
      ? `${selectedName} 此刻尚未形成可觀測的連結`
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
      pair.textContent = `${entityDisplayName(lastEntityMap.get(link.from), link.from)} ↔ ${entityDisplayName(lastEntityMap.get(link.to), link.to)}`;
      const detail = document.createElement("span");
      detail.textContent = "能量連結";
      item.append(pair, detail);
      interactionList.appendChild(item);
    });
}

function historyTimeLabel(event: any): string {
  const epoch = Number(event?.epoch);
  if (Number.isFinite(epoch) && epoch > 0) return `紀元 ${epoch}`;
  const embeddedEpoch = String(event?.message || "").match(/Epoch\s+(\d+)/i)?.[1];
  if (embeddedEpoch) return `紀元 ${embeddedEpoch}`;
  const timestamp = new Date(Number(event?.timestamp) * 1000);
  return Number.isNaN(timestamp.getTime())
    ? "年代未明"
    : timestamp.toLocaleString("zh-TW", {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  birth: "誕生",
  chronicle: "紀元編年",
  creation: "創作",
  death: "消逝",
  epoch_change: "紀元轉換",
  epoch_transition: "紀元轉換",
  evolution: "演化",
  genesis: "創世",
};

function appendHistoryMessage(container: HTMLElement, event: any, milestone = false): void {
  if (!milestone) {
    const type = String(event?.type || "event");
    container.append(document.createTextNode(`[${EVENT_TYPE_LABELS[type] || type}] `));
  }
  const entityIds = Array.isArray(event.entity_ids)
    ? event.entity_ids.filter((id: unknown) => typeof id === "string" && id)
    : [];
  const escapedIds = entityIds.map((id: string) => id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const entityPattern = escapedIds.length > 0 ? new RegExp(`(${escapedIds.join("|")})`, "g") : null;
  const parts = entityPattern ? String(event.message).split(entityPattern) : [String(event.message)];
  parts.forEach((part) => {
    if (entityIds.includes(part)) {
      const entityLink = document.createElement("button");
      entityLink.type = "button";
      entityLink.className = "history-inline-node";
      entityLink.textContent = entityDisplayName(lastEntityMap.get(part), part);
      entityLink.addEventListener("click", () => selectEntity(part));
      container.appendChild(entityLink);
    } else {
      container.append(document.createTextNode(part));
    }
  });
}

function renderHistoryItem(event: any, milestone = false): HTMLElement {
  const item = document.createElement("li");
  if (milestone) item.className = `milestone importance-${Number(event.importance) || 0}`;
  if (event?.type === "chronicle") item.classList.add("chronicle");
  const timeElement = document.createElement("time");
  timeElement.textContent = historyTimeLabel(event);
  const message = document.createElement("span");
  appendHistoryMessage(message, event, milestone);
  item.append(timeElement, message);
  return item;
}

function renderEntityBiography(entity: any, events: any[]): void {
  if (!selectedEntityId) {
    biographyPanel.hidden = true;
    return;
  }

  biographyPanel.hidden = false;
  biographyName.textContent = entityDisplayName(entity, selectedEntityId);
  const kind = [entity?.role, entity?.resource_type, entity?.type]
    .filter((value) => typeof value === "string" && value)
    .join(" · ");
  biographyKind.textContent = kind || "歷史實體";
  biographyState.textContent = entity?.state || "僅存歷史紀錄";
  biographyEventCount.textContent = `${events.length} 筆紀錄`;
  biographyAchievements.innerHTML = "";

  const notableEvents = events.filter((event: any) => (
    event.type !== "dialogue_request"
    && event.type !== "dialogue_response"
    && (Number(event.importance) >= 5 || (Array.isArray(event.entity_ids) && event.entity_ids.length >= 2))
  ));
  const achievements = (notableEvents.length > 0 ? notableEvents : events)
    .filter((event: any) => event.type !== "dialogue_request" && event.type !== "dialogue_response")
    .slice(0, 5);
  if (achievements.length === 0) {
    const empty = document.createElement("li");
    empty.className = "biography-empty";
    empty.textContent = "尚未留下可觀測的事蹟。";
    biographyAchievements.appendChild(empty);
    return;
  }
  achievements.forEach((event: any) => biographyAchievements.appendChild(renderHistoryItem(event)));
}

function renderInteractionActivity(events: any[]): void {
  interactionActivityList.innerHTML = "";
  const interactions = events.filter((event: any) => (
    event.type !== "dialogue_request"
    && event.type !== "dialogue_response"
    && Array.isArray(event.entity_ids)
    && event.entity_ids.length >= 2
  ));
  if (interactions.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty-interaction";
    empty.textContent = "尚未觀測到可記錄的多實體互動。";
    interactionActivityList.appendChild(empty);
    return;
  }
  interactions.slice(0, 5).forEach((event: any) => {
    interactionActivityList.appendChild(renderHistoryItem(event));
  });
}

function newestFirst(events: any[]): any[] {
  return events
    .map((event, index) => ({ event, index }))
    .sort((left, right) => {
      const rightTime = Number(right.event?.timestamp);
      const leftTime = Number(left.event?.timestamp);
      const timeOrder = (Number.isFinite(rightTime) ? rightTime : 0)
        - (Number.isFinite(leftTime) ? leftTime : 0);
      return timeOrder || left.index - right.index;
    })
    .map(({ event }) => event);
}

function renderDialogue(events: any[]): void {
  dialogueLog.hidden = !selectedEntityId;
  dialogueLog.innerHTML = "";
  if (!selectedEntityId) return;

  const dialogueEvents = events
    .filter((event: any) => event.type === "dialogue_request" || event.type === "dialogue_response")
    .reverse();
  if (dialogueEvents.length === 0) {
    const empty = document.createElement("p");
    empty.className = "dialogue-empty";
    empty.textContent = `向 ${selectedEntityDisplayName()} 傳送訊息，開始對話。`;
    dialogueLog.appendChild(empty);
    return;
  }

  dialogueEvents.forEach((event: any) => {
    const line = document.createElement("p");
    line.className = `dialogue-line ${event.type === "dialogue_response" ? "response" : "request"}`;
    line.textContent = event.type === "dialogue_response"
      ? `${selectedEntityDisplayName()}：${String(event.message).replace(/^\[[^\]]+\] 回應：/, "")}`
      : `你：${String(event.message).replace(/^\[[^\]]+\] 收到觀測者訊號：/, "")}`;
    dialogueLog.appendChild(line);
  });
  dialogueLog.scrollTop = dialogueLog.scrollHeight;
}

// === 2. WebSocket 宇宙串流監聽 ===
function initWebSocket() {
  if (
    isPaused
    || universeSocket?.readyState === WebSocket.OPEN
    || universeSocket?.readyState === WebSocket.CONNECTING
  ) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.port === "5173" ? "127.0.0.1:8000" : window.location.host;
  const wsUrl = `${protocol}//${host}/ws/universe`;

  const ws = new WebSocket(wsUrl);
  universeSocket = ws;

  ws.onmessage = (event) => {
    if (universeSocket !== ws || isPaused || pauseControlPending) return;
    try {
      const data = JSON.parse(event.data);
      if (data.runtime?.paused) {
        disconnectRealtime();
        renderEvolutionStatus(data.runtime);
        return;
      }
      startRealtimeRequests();
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
          setDialogueAvailability(lockedEntity);
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
          id.textContent = entityDisplayName(lastEntityMap.get(m.id), String(m.id));
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
        statEpoch.textContent = `模擬紀元: ${data.scene.metrics.epoch || 0}`;
        updateCosmicReflection(data.scene.metrics);
      }

      // 同步 CLI、Model 與速度設定狀態
      if (data.runtime) {
        renderEvolutionStatus(data.runtime);
        const activeModelIsAvailable = Array.from(modelSelect.options).some(
          (option) => option.value === data.runtime.active_model,
        );
        const isChoosingControl = (
          document.activeElement === cliSelect
          || document.activeElement === modelSelect
          || isCLIChangePending
          || isModelChangePending
        );
        if (!isChoosingControl) {
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

  ws.onclose = (event) => {
    if (universeSocket !== ws) return;
    universeSocket = null;
    if (event.reason === "宇宙已暫停") {
      disconnectRealtime();
      renderEvolutionStatus({ paused: true });
      return;
    }
    if (!isPaused && websocketReconnectTimer === null) {
      console.warn("[WebSocket] 連線中斷，3 秒後嘗試重新連線...");
      websocketReconnectTimer = window.setTimeout(() => {
        websocketReconnectTimer = null;
        initWebSocket();
      }, 3000);
    }
  };
}

initWebSocket();

// === 3. 歷史事件定時拉取 ===
async function fetchHistory() {
  if (isPaused || !networkReady || historyRequestPending) return;
  historyRequestPending = true;
  const requestedEntityId = selectedEntityId;
  try {
    const params = new URLSearchParams({ limit: "15" });
    if (selectedEntityId) params.set("entity_id", selectedEntityId);
    const res = await appFetch(`/api/habitat/history?${params}`);
    if (res.ok) {
      const data = await res.json();
      if (requestedEntityId !== selectedEntityId) return;
      eventsList.innerHTML = "";
      milestonesList.innerHTML = "";
      historyScope.textContent = selectedEntityId ? `${selectedEntityDisplayName()} 的歷程` : "全域事件";
      const stage = typeof data.civilization_stage === "string" ? data.civilization_stage.trim() : "";
      timelineMeta.textContent = `目前紀元 ${Number(data.current_epoch) || 0}${stage ? ` · ${stage}` : ""}`;
      if (Array.isArray(data.milestones) && data.milestones.length > 0) {
        newestFirst(data.milestones).forEach((event: any) => {
          milestonesList.appendChild(renderHistoryItem(event, true));
        });
      } else {
        const empty = document.createElement("li");
        empty.className = "timeline-empty";
        empty.textContent = "尚未形成可記錄的大事。";
        milestonesList.appendChild(empty);
      }
      if (Array.isArray(data.events)) {
        const events = newestFirst(data.events);
        renderEntityBiography(selectedEntityId ? lastEntityMap.get(selectedEntityId) : null, events);
        renderInteractionActivity(events);
        renderDialogue(events);
        events.forEach((event: any) => eventsList.appendChild(renderHistoryItem(event)));
      }
    }
  } catch (err) {
    // 忽略未連線或主動取消的錯誤。
  } finally {
    historyRequestPending = false;
  }
}

// === 4. 控制面板事件綁定 ===

// 切換 CLI 適配器
cliSelect.addEventListener("change", async () => {
  const targetCli = cliSelect.value;
  isCLIChangePending = true;
  controlRevision += 1;
  try {
    const res = await appFetch("/api/control/cli", {
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
    if (isRequestCancelled(err)) return;
    alert("與後端通訊失敗");
  } finally {
    isCLIChangePending = false;
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
  controlRevision += 1;
  try {
    const res = await appFetch("/api/control/model", {
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
    if (isRequestCancelled(err)) return;
    alert("與後端通訊失敗");
  } finally {
    isModelChangePending = false;
  }
});

async function setEffort(cli: string, effort: string) {
  try {
    const res = await appFetch("/api/control/effort", {
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
    if (isRequestCancelled(err)) return;
    alert("與後端通訊失敗");
    initModelsList();
  }
}

codexEffortSelect.addEventListener("change", () => setEffort("codex", codexEffortSelect.value));
claudeEffortSelect.addEventListener("change", () => setEffort("claude", claudeEffortSelect.value));

async function sendOracleSignal() {
  if (!isDialogueAvailable || !selectedEntityId) {
    oracleFeedback.textContent = "請先選擇有意識的生命體再對話。";
    return;
  }
  const message = oracleInput.value.trim();
  if (!message) return;

  try {
    oracleFeedback.textContent = "傳送中…";
    const res = await appFetch("/api/observer/signal", {
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
    if (historyRefreshTimer !== null) window.clearTimeout(historyRefreshTimer);
    historyRefreshTimer = window.setTimeout(() => {
      historyRefreshTimer = null;
      void fetchHistory();
    }, 600);
  } catch (err) {
    if (isRequestCancelled(err)) return;
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
      const res = await appFetch("/api/control/speed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed }),
      });
      if (res.ok) {
        speedBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
      }
    } catch (err) {
      if (isRequestCancelled(err)) return;
      console.error("設定速度失敗：", err);
    }
  });
});

// 暫停時先切斷觀測連線，只保留這一筆必要的控制請求。
pauseBtn.addEventListener("click", async () => {
  if (pauseControlPending) return;
  const nextState = pauseNeedsRetry || !isPaused;
  pauseControlPending = true;
  disconnectRealtime();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  try {
    const res = await fetch("/api/control/pause", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paused: nextState }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.paused !== nextState) throw new Error("後端狀態與請求不一致");
    isPaused = data.paused;
    pauseNeedsRetry = false;
    renderEvolutionStatus({ paused: isPaused });
    oracleFeedback.textContent = isPaused ? "已暫停並中斷連線。" : "已恢復連線。";
  } catch (err) {
    pauseNeedsRetry = nextState;
    oracleFeedback.textContent = nextState
      ? "前端已斷線，但後端暫停尚未確認。請按「重試暫停」。"
      : "恢復未確認，前端保持斷線。請重試恢復。";
    renderEvolutionStatus({ scheduler_status: {
      status: nextState ? "pause_failed" : "resume_failed", reason: oracleFeedback.textContent,
    } });
  } finally {
    window.clearTimeout(timeout);
    pauseControlPending = false;
    if (!isPaused) {
      // 恢復 API 已確認後即可重建 HTTP 控制通道，不必等待第一個 WebSocket 封包。
      // 否則串流連線延遲或失敗時，模型與速度選單會永久維持停用。
      startRealtimeRequests();
      initWebSocket();
    } else {
      renderConnectionControls();
    }
  }
});
