#!/usr/bin/env bash
# ==========================================
# Evo（衍界）一鍵啟動腳本 (macOS 雙擊啟動檔)
# ==========================================

# 自動切換到專案根目錄
cd "$(dirname "$0")"

echo "=================================================="
echo "        🚀 正在啟動 Evo（衍界）宇宙觀測系統..."
echo "=================================================="

# 檢查虛擬環境
if [ ! -f ".venv/bin/uvicorn" ]; then
    echo "⚠️ 尚未偵測到 Python 虛擬環境，正在自動建立..."
    uv venv .venv
    source .venv/bin/activate
    uv pip install -e ".[dev]"
fi

# 1.5 秒後自動在預設瀏覽器開啟觀測介面
(sleep 1.5 && open "http://127.0.0.1:8000") &

# 啟動 Evo 核心伺服器
echo "📡 正在監聽 http://127.0.0.1:8000 ..."
echo "💡 若要關閉系統，請按 Ctrl + C"
echo "--------------------------------------------------"
.venv/bin/uvicorn observer.server.app:app --host 127.0.0.1 --port 8000 --no-access-log

