#!/usr/bin/env bash
# Evo（衍界）一鍵啟動腳本
cd "$(dirname "$0")"

# 1.5 秒後自動在瀏覽器開啟
(sleep 1.5 && open "http://127.0.0.1:8000") &

# 啟動服務（關閉頻繁的 HTTP 請求訪問日誌，保持終端機乾淨）
.venv/bin/uvicorn observer.server.app:app --host 127.0.0.1 --port 8000 --no-access-log

