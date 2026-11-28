#!/bin/sh
# ダブルクリックで確認アプリを開く。もう動いていればブラウザで開くだけ、止まっていれば起動してから開く。
cd "$(dirname "$0")/../.." || exit 1
PORT=8765
ID=$(curl -s --max-time 2 "http://127.0.0.1:$PORT/app-id" 2>/dev/null)
if [ "$ID" = "keiba-confirm-app" ]; then
  open "http://127.0.0.1:$PORT/"
elif curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/"; then
  echo "ポート $PORT で別のものが動いているので、開かないよ。止めてからもう一度ダブルクリックしてね" >&2
  exit 1
else
  echo "確認アプリを起動するよ（この窓を閉じると止まる）"
  exec bin/keiba app --open --notify --port "$PORT"
fi
