#!/bin/sh
# Macにログインしたら確認アプリを自動で起動する（launchd）。127.0.0.1 だけ・通知あり。
#   sh tools/app/autostart.sh install    入れる（すぐ起動もする）
#   sh tools/app/autostart.sh install --lan  同じWi-FiのiPhoneからも開ける（.env の KEIBA_APP_PASSPHRASE が要る）
#   sh tools/app/autostart.sh uninstall  外す（plist はゴミ箱へ）
#   sh tools/app/autostart.sh status     動いているか
set -e
LABEL=com.heroines.keiba-app
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$REPO/data/app/app.log"
LAN=""
[ "$2" = "--lan" ] && LAN=" --lan"
case "$1" in
install)
  mkdir -p "$REPO/data/app"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string><string>cd "$REPO" &amp;&amp; { bin/keiba app --help 2>/dev/null | grep -q -- --notify || { echo "確認アプリが無い版なので起動しない（ブランチを確認してね）"; exit 0; }; } &amp;&amp; exec bin/keiba app --notify${LAN} --port 8765</string></array>
<key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>ThrottleInterval</key><integer>30</integer>
<key>StandardOutPath</key><string>$LOG</string>
<key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PL
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "入れたよ：${PLIST}（ログ ${LOG}）"
  ;;
uninstall)
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    if ! launchctl bootout "gui/$(id -u)/$LABEL"; then
      echo "止められなかったよ（launchctl bootout が失敗）。plist はそのまま残した：${PLIST}" >&2
      exit 1
    fi
  fi
  if [ -f "$PLIST" ]; then
    if command -v trash >/dev/null; then trash "$PLIST"; else mv "$PLIST" "$HOME/.Trash/"; fi
  fi
  if [ -f "$PLIST" ] || launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "外しきれなかったよ。status で確かめてね" >&2
    exit 1
  fi
  echo "外したよ（plist はゴミ箱）"
  ;;
status)
  if INFO=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null); then
    echo "入っているよ：${PLIST}"
    echo "$INFO" | grep -E "^[[:space:]]*(state|pid) =" | head -2
    if curl -s --max-time 2 "http://127.0.0.1:8765/app-id" | grep -q "keiba-confirm-app"; then
      echo "応答あり：http://127.0.0.1:8765/"
    else
      echo "まだ応答が無いよ（ログ ${LOG}）"
    fi
  else
    echo "入っていないよ（自動起動は未登録）"
    exit 3
  fi
  ;;
*) echo "使い方: sh ${0} install|uninstall|status"; exit 2 ;;
esac
