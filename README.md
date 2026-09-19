# Evo（衍界）

AI 自主發展宇宙觀測系統。外層控制器負責初始化、審查、隔離、部署與復原；`runtime/habitat/` 是由 AI 自行維護的生態池。

## 空白創世

空白或不存在的 `runtime/habitat/` 是有效初始狀態，不需要預裝 `main.py`、種子世界或世界專用 helper。
外層認知調度器會呼叫目前設定的 CLI，直接在 `runtime/habitat/` 建立第一版程式。AI 自行決定世界規則、角色、模組、agent、skill、記憶與工具的組織方式，沒有預設劇情或檔案數要求。

`main.py` 是固定執行入口；世界狀態保存在 `habitat.db`。後續演化沿用正式資料庫，首次部署則保留候選建立的資料庫。AI 程式必須能從資料庫恢復狀態，不能每次啟動重建世界。

## 安裝與啟動

需要 Python 3.11+、Node.js，以及已安裝並登入的 CLI（agy、codex 或 claude）。
目前世界執行隔離支援 macOS 的 `sandbox-exec`；原生隔離不可用時拒絕執行，Windows 與 Linux 尚不支援。

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cd observer/web
npm install
npm run build
cd ../..
.venv/bin/uvicorn observer.server.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

瀏覽器開啟 http://127.0.0.1:8000 。服務啟動會開始認知調度；已有入口時也會啟動世界程序。
控制 API 沒有登入驗證，服務僅供本機使用，請維持 loopback 綁定。

CLI 沿用主機現有登入。切換 CLI 或模型不重建世界；模型清單依適配器取得，不在文件固定列出易過期的模型名稱。設定會持久保存，CLI 不可用時回報錯誤。

## 生成、審查與啟動

1. 外層先停止世界並建立復原快照，再讓 CLI 直接修改 `runtime/habitat/`；空白時產生世界，已有世界時修改程式。
2. 生成提示要求 AI 自行 code review；Guardian 以隔離設定的 Ruff 檢查候選 Python 程式（E9、F），不接受候選的 Ruff 設定或 noqa 關閉檢查。
3. 有套件需求時，由外層受控安裝核准 wheel。
4. 程式全部寫完後，必須通過原生沙箱冒煙測試及觀測資料檢查，才由 Supervisor 啟動。
5. 生成、驗證或啟動失敗時，以回合開始前的快照復原程式與記憶資料。

目前沒有另一個獨立 AI 審查者。靜態檢查與短時間冒煙測試不能證明任意生成程式永遠正確，也不保證文明一定持續成長。
快照保留最近三組；它們是復原用資料，不是完整世界歷史。

## 沙箱與套件

世界程序可讀寫 `runtime/habitat/` 內的檔案，包含自行建立的 agents、skills 與隱藏目錄；必要的 Python 執行環境以唯讀方式開放。
原生沙箱限制外部檔案、網路及子程序；Python audit hook 是額外防線。資源監控會處理失控程序。

生成程式用的 CLI 在外層執行並沿用主機登入，**目前沒有套用世界程序的 OS 沙箱**。
提示中的工作區限制不是 CLI 的作業系統安全保證。

世界需要額外套件時，可建立 `sandbox-requirements.txt`，每行使用 `名稱==精確版本`。
目前 allowlist 為 Pillow、Mido、MIDIUtil、NumPy、NetworkX。外層僅從 PyPI 安裝 binary wheel，不執行 source build、不遞迴安裝依賴；沒有符合平台的 wheel 時安裝失敗。
套件存放於 `.evo-packages/`，正式世界仍離線執行。套件 bootstrap 需要控制器 Python 環境已安裝 pip。

## 觀測資料協議

世界自行建立 SQLite schema。前後端透過以下固定協議溝通；控制器不匯入 habitat 的 Python 模組，也不保留舊宇宙 schema 的相容分支。

- `scene_primitives(id TEXT PRIMARY KEY, json_data TEXT, updated_at REAL)`：`current_scene` 記錄提供場景 JSON。
- `world_state(key TEXT PRIMARY KEY, value TEXT)`：`metrics` 值為 JSON，可包含 `epoch`、`civilization_stage`。
- `events(id TEXT PRIMARY KEY, type TEXT, message TEXT, importance INTEGER, timestamp REAL, entity_ids TEXT, epoch INTEGER)`：`entity_ids` 為 JSON ID 陣列。
- `observer_signals(id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, message TEXT, target_entity_id TEXT, timestamp REAL, processed INTEGER DEFAULT 0, delivered_to_universe INTEGER DEFAULT 0)`：觀測者信箱。
- 選用作品表 `creative_works(id TEXT PRIMARY KEY, creator_id TEXT, title TEXT, medium TEXT, mime_type TEXT, content BLOB, metadata_json TEXT, epoch INTEGER, created_at REAL)`：保存完整作品原文或原始位元組；世界自行實作寫入，不預装 helper。

場景包含 `grid`、`entities`、`links`。實體提供 ID、位置、大小、顏色與 label，目前畫布支援圓形與矩形。
世界須持續寫入場景、狀態與事件；若未記錄事件，時間線與大事記便沒有資料。所有提供給觀測者的事件訊息、大事記、編年與紀元名稱皆以繁體中文記錄；`id`、`type` 等機器欄位可維持 ASCII 識別字。大事記只收錄紀元轉換（`epoch_transition`／`epoch_change`）及其正式編年（`chronicle`）；個體的誕生、創作、互動、發現等事件，不論重要程度，一律保留在一般宇宙事件。

完整生成要求見 [prompts/base.md](prompts/base.md)。世界自訂的 skill 不能覆蓋外層安全邊界。

## 操作

- 上方可切換 CLI、模型及 1x／3x／MAX；3x 為精準三倍速，MAX 為每秒最多 20 ticks 的安全上限。速度只影響世界 tick；AI 改程式固定保留 5 分鐘冷卻，避免快轉時反覆停下宇宙。每個 tick 都不會呼叫模型。
- 暫停會取消 AI 工作、停止世界程序並斷開前端持續連線；恢復後重新連接。
- 點選實體查看狀態與相關歷史，點空白回到全域。
- 拖曳模式按鈕可再次點擊取消；按住空白鍵配合滑鼠左鍵可暫時平移，放開後恢復選取。
- 觀測者訊號會寫入資料庫，交給世界／後續認知處理；送出成功不代表 AI 已經回應。
- 作品 API 提供中繼資料與原始內容。HTML、SVG 等主動內容強制下載並套用限制標頭。

## 開發期間維護外層程式

可以持續修改外層控制器與觀測介面，不需要手動修改 `runtime/habitat/`。純顯示調整通常不影響世界；修改模型、提示、認知節拍、審查規則或資料協議，則可能改變後續演化方向或互動行為。

沒有使用自動重載時，修改 Python 檔案通常不會立即影響已載入的服務。需要套用後端變更時，先暫停、停止服務，再修改並重新啟動；避免在世界運行中使用開發用自動重載。

暫停或重啟會中斷演化，尚未完成的 AI 候選可能被取消。已寫入資料庫的狀態應由世界程式在啟動時讀回；尚未持久化的記憶體狀態可能遺失。這取決於生成程式的保存與恢復實作，不能保證每個候選都能完整延續。

## 驗證

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q tests/
cd observer/web
npm test
npm run build
```

原生沙箱測試須在允許啟動 sandbox-exec 的 macOS 環境執行。測試使用暫存工作區與模擬 CLI，不依賴某個已生成的宇宙。
測試通過不等於已完成真實模型端到端創世；登入、CLI 可用性及生成品質仍須實際執行確認。

## 完整重置

所有可重置的執行期資料集中在 `runtime/`：

```text
runtime/
├── habitat/   # 世界程式、habitat.db、記憶與受控套件
├── history/   # CLI／模型／速度設定與認知回合日誌
└── backups/   # 最近三組復原快照
```

完整 reset 時先停止服務與世界程序，再清空整個 `runtime/`；不需要手動重建任何子目錄。下次啟動與首次創世會自動建立 `runtime/habitat/`，設定及快照需要寫入時也會自行建立 `runtime/history/` 與 `runtime/backups/`，並以預設設定從空白宇宙重新開始。原始碼、測試、`.venv/` 與 `observer/web/node_modules/` 不屬於 reset 範圍。

從舊版升級時，先停止仍在運行的舊服務；專案根目錄若殘留 `habitat/`、`history/` 或 `backups/`，它們不會再被新版本讀取，可在確認不需保留後自行刪除。

## 目錄

- `cli/`：CLI 適配器與取消控制。
- `core/`：認知調度、上下文、資源、審查、快照與程序管理。
- `isolation/`：原生隔離、驗證與套件 bootstrap。
- `observer/`：觀測橋接、FastAPI 與前端。
- `prompts/`、`skills/`：外層提供的生成指引。
- `runtime/`：可完整 reset 的世界程式、記憶、控制設定、日誌與快照。
- `tests/`：控制器與通用協議測試。
