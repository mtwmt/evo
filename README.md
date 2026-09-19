# Evo（衍界）

> AI 自主發展宇宙觀測系統。核心治理系統、安全沙盒、雙軌節拍引擎、創世種子與 Phaser 觀測前端已完整實作並驗證完畢。

---

## 1. 核心原則

- `Evo` 是外層控制系統；`habitat/` 是 AI 自己成長的宇宙。
- 除了技術與安全邊界，宇宙內容與發展都由 AI 自己定義。
- 不預設角色、文明、國號、曆法、物理規則或發展目標。
- 使用者主要是觀察者，可看世界、歷史、程式碼，也可發送訊號／與 AI 對話（作為外在刺激輸入 Context）。
- CLI 可替換；切換 AI 不重建宇宙。
- 程式碼與所有模組註解嚴格採用繁體中文。

---

## 2. 快速開始與使用方式

### (1) 環境需求與依賴安裝

專案需 Python 3.11+ 及 Node.js 18+。

```bash
# 1. 建立 Python 虛擬環境並安裝依賴
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# 2. 前端依賴安裝與生產環境建構（已預先編譯至 dist/）
cd observer/web
npm install
npm run build
cd ../..
```

### (2) 一鍵啟動觀測系統

執行下列指令即可啟動 Evo 核心服務：

```bash
.venv/bin/uvicorn observer.server.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

啟動後，系統會自動在安全沙盒中運作 `habitat/main.py` 物理進程，並同步啟動非同步 AI 認知節拍調度。

### (3) 瀏覽器觀測與操作指南

開啟瀏覽器造訪 **`http://127.0.0.1:8000`**：

1. **Phaser 2D 畫布**：
   * 實時繪製自生宇宙的抽象幾何原語（質點、能量流、動態連線、數值標籤）。
2. **模擬速度調節**：
   * 點擊頂部按鈕切換 `1x`（正常）、`3x`（快速）、`MAX`（極速無延遲）或 `暫停`。
3. **天外神諭介入（與 AI 對話）**：
   * 於右側面板輸入文字訊號，系統會將其寫入信箱佇列，在下一個 AI Cognitive Heartbeat 作為天外神諭刺激注入 Context。
4. **CLI 適配器與 Model 模型切換**：
   * 下拉選單可在 `agy`（預設）、`codex`、`claude` 之間切換。
   * 模型下拉選單隨所選 CLI 動態載入專屬支援模型（如 agy 的 `gemini-2.5-pro`、`gemini-2.5-flash`；codex 的 `o3-mini`、`gpt-4o`；claude 的 `claude-3-7-sonnet-latest` 等）。
   * 若選擇之 CLI 尚未安裝，系統會明確阻斷切換且**絕不自動 fallback**。
5. **唯讀程式碼檢視器**：
   * 點擊按鈕可隨時在瀏覽器中查看由 AI 實時演化出的 `habitat/` 程式碼檔案。

### (4) 測試與安全驗證

```bash
# 1. 執行全套單元測試（包含 API、CLI、Guardian、隔離層、回滾與排程）
.venv/bin/pytest -v tests/

# 2. 執行啟動前主動安全越界滲透測試
.venv/bin/python -m isolation.verifier

# 3. 程式碼品質與風格檢查
.venv/bin/ruff check .
```

---

## 3. 技術棧

- **Backend / Core**：Python 3.11+、FastAPI、Pydantic、Psutil
- **Universe Code**：Python（預裝通用科學/圖論基礎庫如 `numpy`、`networkx`，嚴格禁止動態 `pip install`）
- **Database**：SQLite（唯一 DB：`habitat/habitat.db`，部署前自動快照備份）
- **Frontend**：TypeScript + Phaser 3 + Vite（位於 `observer/web/`，編譯產物由 FastAPI 託管）
- **即時通訊**：WebSocket（以 20 FPS 串流幾何原語與系統遙測數據）
- **支援平台**：macOS / Windows
- **邊界限制**：不使用 Docker / 不使用 Ollama
- **CLI 適配**：預設 agy，支援 Codex、Claude 或其他 CLI Adapter

---

## 4. 系統架構

```text
    ┌───────────────────────────┐
    │  Observer (Web / Dialog)  │
    └─────────────┬─────────────┘
                  │ 觀察者訊號 / 神諭介入
                  ▼
         Context Manager ◄────────────────┐
                  │                       │
      Base Prompt + Skills + Context      │
                  │                       │
             CLI Adapter                  │
        (agy / Codex / Claude)            │
                  │                       │
            Isolation 沙盒                 │
                  │                       │
          Candidate Staging               │
         (habitat_staging/)               │
                  │                       │
   Tests (Ruff + Smoke) + Code Review     │
                  │                       │
           通過 ──┴── 失敗 (最多修復 3 輪)  │
           │           │                  │
           │           └─ 放棄 Candidate  │
           ▼                              │
      DB Snapshot 快照                    │
           ▼                              │
     Atomic Deploy (部署至 habitat/)      │
           ▼                              │
    Runtime Supervisor                    │
           ▼                              │
     habitat/main.py ◄────────────────────┘ (Tick 物理循環)
           │
           ▼ (持續運作)
   habitat.db + 通用渲染原語
           │
           ▼
     Observer Bridge (FastAPI / WebSocket)
           │
           ▼
     Phaser (2D 畫布純原語渲染)
```

- **Cognitive Heartbeat（認知節拍）**：負責 AI「思考、反省、改寫代碼與推進世界設計」。
- **Universe Tick（宇宙物理時鐘）**：負責讓已經部署的 `habitat/main.py` 連續推進宇宙運作與數據計算。

---

## 5. 專案目錄架構

```text
evo/
├─ core/
│  ├─ heartbeat/     # 雙軌節拍調度 (Cognitive Heartbeat & Tick 控制)
│  ├─ autonomous/    # 自主決策推進
│  ├─ context/      # Context Manager 與神諭/觀察者訊息注入
│  ├─ review/       # 外層靜態/冒煙檢查與 Guardian 守門員
│  ├─ runtime/      # main.py 生命週期管理、Staging 與 Atomic 替換
│  ├─ process/      # 子程序隔離執行
│  ├─ resource/     # 資源監控與 Governor (CPU 25%, RAM 1GB)
│  └─ lifecycle/    # 啟動、停止、快照與 Rollback
├─ cli/
│  ├─ adapters/     # CLI 適配抽象基底介面 (BaseCLIAdapter)
│  ├─ agy/          # 預設 agy 適配層
│  ├─ codex/        # Codex 適配層
│  ├─ claude/       # Claude 適配層
│  └─ factory.py    # 適配器工廠與動態切換（無 fallback）
├─ skills/
│  ├─ autonomous/   # 自主發展引導 SKILL.md
│  ├─ review/       # 審查與重構標準 SKILL.md
│  ├─ migration/    # DB 與代碼平滑遷移指引 SKILL.md
│  └─ history/      # 歷史紀錄與摘要歸納 SKILL.md
├─ prompts/
│  └─ base.md       # 極簡核心 Prompt
├─ isolation/
│  ├─ common/       # Python sys.addaudithook 攔截與抽象沙盒
│  ├─ windows/      # Windows AppContainer / LPAC 實作
│  ├─ macos/        # macOS 沙盒與權限隔離實作
│  └─ verifier.py   # 啟動前主動越界滲透測試器
├─ observer/
│  ├─ bridge/       # DB / Event 事件橋接器 (ObserverBridge)
│  ├─ server/       # FastAPI WebSocket 與 REST 控制伺服器
│  └─ web/          # Vite + TypeScript + Phaser 前端應用
│     ├─ src/       # 前端原始碼 (CosmicScene.ts, main.ts, style.css)
│     └─ dist/      # 生產環境編譯產物
├─ habitat/         # AI 自生宇宙工作空間 (受沙盒保護)
│  ├─ main.py       # 創世種子主程式
│  └─ habitat.db    # 宇宙唯一資料庫
├─ history/         # 歷史事件與壓縮摘要檔案
├─ backups/         # DB 快照備份與 Stable 代碼歷史
├─ config/          # 外層系統設定 (settings.py)
├─ tests/           # 15 項自動化單元測試套件
├─ pyproject.toml   # 專案依賴與工具設定
└─ README.md        # 完整說明文件
```

---

## 6. 重要規則

### 雙軌節拍（Heartbeat & Universe Tick）

系統將「物理時鐘」與「AI 思考」解耦：

1. **Universe Tick（`main.py` 物理時鐘）**：
   - `1x`：1 秒 / tick
   - `3x`：0.3 秒 / tick
   - `MAX`：0.05 秒 / tick
2. **Cognitive Heartbeat（AI 思考與改碼冷卻）**：
   - `1x`：間隔 60 秒
   - `3x`：間隔 15 秒
   - `MAX`：上一輪結束後間隔 3 秒
   - 冷卻從上一輪完成後開始計時，同一時間嚴格只跑一個 AI 回合。
   - **休眠機制**：AI 可在輸出中聲明 `STATUS: SLEEP <N>`（維持現狀，靜默觀察 N 個週期），在此期間不重複呼叫 LLM，避免 Token 與 Quota 無謂消耗。

### CLI 適配

- 第一次啟動預設 `agy`。
- 之後沿用上一次使用的 CLI。
- 運行中可動態切換 CLI，下一個 Cognitive Heartbeat 生效。
- 世界、DB、歷史與 Context 絕不重建。
- CLI 不可用時顯示錯誤，嚴格**不自動 fallback**。

### habitat 與執行環境

- 固定入口：`habitat/main.py`。
- 唯一 DB：`habitat/habitat.db`。
- DB schema / table / 欄位由 AI 自己演化。
- **依賴限制**：環境預裝常用基礎庫（Python 標準庫、`numpy`、`networkx` 等穩定計算庫），**嚴格禁止 AI 自行 `pip install` 或下載外部執行檔**。
- 其他目錄與程式內容由 AI 自己產生。

### 安全部署與 Code Review（Staging & Verification）

```text
Candidate (在 habitat_staging/ 產生)
→ 1. 靜態分析 (ruff check 語法與無效匯入)
→ 2. 冒煙測試 (沙盒背景試跑 5~10 秒無崩潰、DB 正常連線)
→ 3. 邊界測試 (無越界檔案存取與非法 syscall)
→ 4. AI Code Review
→ 通過驗證 ──┬── 通過：快照 DB → Atomic 替換至 habitat/ → 重啟 main.py
              └── 失敗：最多自我修復 3 輪，仍失敗則放棄 Candidate
```

- 仍失敗則放棄 Candidate，保留現有 Stable Version。
- 部署或 DB migration 失敗時，透過 SQLite 快照與備份代碼進行一鍵完整 rollback。
- Review 只改善實作品質與系統穩定性，不干預宇宙發展方向。

### Resource Governor

預設硬性限制：

- CPU：約 25%
- RAM：1 GB
- 子程序：最多 4 個
- `habitat`：最多 2 GB
- AI 不能自行放寬限制。

### Observer 與通用渲染原語協議

Phaser **只負責顯示幾何原語，不預設 Person、Kingdom、Tree 等具體業務概念**。

`habitat` 透過通用原語（Generic Scene Primitives）向前端推送視覺狀態：

- **Grid / Canvas**：畫布邊界、背景色、網格維度。
- **Entities**：位置 `(x, y)`、幾何形狀（`circle` / `rect` / `polygon`）、尺寸、顏色、透明度、文字標籤。
- **Links**：實體間連線（起點、終點、顏色、樣式、線寬）。
- **Particles / FX**：基本粒子效果（爆發、流動）。
- **Metrics & Logs**：AI 自定義的世界數值面板與即時文字事件流。

### 觀察者介入（Observer Signals）

- 觀察者在 Web 介面輸入的訊息或提問，寫入 `habitat.db` 的信箱佇列。
- Context Manager 在下一個 Cognitive Heartbeat 將該訊息包裝為「外在天外訊號 / 觀測者之聲」注入 Context，由 AI 自行決定如何理解或回應。

### 歷史

- 近期事件保留詳細內容。
- 舊歷史定期由 AI 或 Context Manager 壓縮成摘要。
- 重大歷史里程碑永久保留。
- 嚴格控制 Context 體積，避免 Token 無限膨脹。

### Isolation

- AI 與子程序只能把 `habitat`（或測試時的 `habitat_staging`）當作可讀寫工作空間。
- Windows：AppContainer / LPAC 方向。
- macOS：進程權限降權 + Python 層級 Audit Hooks (`sys.addaudithook`) 雙重防護。
- 啟動前執行越界測試；Isolation 失敗則拒絕啟動宇宙。
- CLI 所需的最小系統／登入資源由 Evo 外層處理，不對 habitat 開放一般系統權限。

---

## 7. AI Context

採用分層架構：

```text
Short Base Prompt
+ Context Manager (世界現狀、觀察者訊息、近期日誌)
+ On-demand Skills (需要時動態加載)
```

Skills 包含：

- `autonomous`：如何自主定義規則、推進世界迭代。
- `review`：代碼自檢、重構原則與邊界規範。
- `migration`：SQLite Schema 遷移與向前相容指南。
- `history`：歷史壓縮與重大事件標記指引。

以 agy 為主要驗證 CLI，但核心設計不綁死 agy。

---

## 8. 驗收完成與驗證狀態

- [x] **安全沙盒隔離驗證**：已通過 `isolation.verifier` 主動越界滲透測試，外部寫入與非法進程呼叫均被攔截。
- [x] **CLI 適配器切換與無 Fallback 機制**：已通過單元測試，無效適配器精確報錯。
- [x] **SQLite 快照與一鍵 Rollback**：已通過單元測試，候選版本異常時可完整無損還原代碼與資料庫。
- [x] **Guardian 守門員冒煙測試**：已通過單元測試，精準阻截語法錯誤與崩潰代碼。
- [x] **Phaser 通用渲染原語端到端整合**：前端已編譯整合完成，並支援 20 FPS WebSocket 串流展示。
- [x] **全自動單元測試套件**：15/15 項測試全數 PASS。
