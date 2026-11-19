#!/bin/sh
# ダブルクリックで確認アプリを開く。もう動いていればブラウザで開くだけ、止まっていれば起動してから開く。
cd "$(dirname "$0")/../.." || exit 1
PORT=8765
if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/favicon.ico"; then
  open "http://127.0.0.1:$PORT/"
else
  echo "確認アプリを起動するよ（この窓を閉じると止まる）"
  exec bin/keiba app --open --notify --port "$PORT"
fi
