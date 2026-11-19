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
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  if [ -f "$PLIST" ]; then
    if command -v trash >/dev/null; then trash "$PLIST"; else mv "$PLIST" "$HOME/.Trash/"; fi
  fi
  echo "外したよ（plist はゴミ箱）"
  ;;
status)
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|pid" | head -3 || echo "入っていないよ"
  ;;
*) echo "使い方: sh ${0} install|uninstall|status"; exit 2 ;;
esac
