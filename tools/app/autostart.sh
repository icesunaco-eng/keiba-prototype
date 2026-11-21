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
# 登録の状態：loaded（入っている）／absent（入っていない）／unknown（調べられなかった）
job_state() {
  if OUT=$(launchctl print "gui/$(id -u)/$LABEL" 2>&1); then echo loaded; return; fi
  case "$OUT" in
    *"Could not find service"*|*"could not find service"*|*"No such process"*) echo absent ;;
    *) echo unknown ;;
  esac
}
case "$1" in
install)
  mkdir -p "$REPO/data/app" "$(dirname "$PLIST")"
  # 新しい plist は一時フォルダに書き、登録できてから置き換える（失敗したら今の plist をそのまま残す）
  TMPD=$(mktemp -d "${TMPDIR:-/tmp}/keiba-app.XXXXXX")
  NEW="$TMPD/$LABEL.plist"
  cat > "$NEW" <<PL
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
  ST=$(job_state)
  if [ "$ST" = unknown ]; then
    rm -rf "$TMPD" || true
    echo "自動起動の状態を調べられなかったので、入れ直すのをやめたよ。今の plist はそのまま：${PLIST}" >&2
    exit 1
  fi
  if [ "$ST" = loaded ] && ! launchctl bootout "gui/$(id -u)/$LABEL"; then
    rm -rf "$TMPD" || true
    echo "今の自動起動を止められなかったよ（launchctl bootout が失敗）。今の plist はそのまま：${PLIST}" >&2
    exit 1
  fi
  if ! launchctl bootstrap "gui/$(id -u)" "$NEW"; then
    rm -rf "$TMPD" || true
    echo "新しい設定で登録できなかったよ（launchctl bootstrap が失敗）。plist は前のまま：${PLIST}" >&2
    if [ "$ST" = loaded ] && [ -f "$PLIST" ]; then
      if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
        echo "前の設定で登録し直したよ" >&2
      else
        echo "前の設定でも登録し直せなかったよ。sh ${0} status で確認してね" >&2
      fi
    fi
    exit 1
  fi
  if ! mv "$NEW" "$PLIST"; then
    # 動いている設定とディスクの設定がずれないように、新しい登録を外して前の状態に戻す
    echo "新しい plist を置けなかったよ（${PLIST}）。新しい登録を外して前の状態に戻すね" >&2
    launchctl bootout "gui/$(id -u)/$LABEL" || echo "新しい登録を外せなかったよ。sh ${0} status で確認してね" >&2
    if [ "$ST" = loaded ] && [ -f "$PLIST" ]; then
      if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
        echo "前の設定で登録し直したよ" >&2
      else
        echo "前の設定でも登録し直せなかったよ。sh ${0} status で確認してね" >&2
      fi
    fi
    rm -rf "$TMPD" || true
    exit 1
  fi
  rm -rf "$TMPD" || true
  echo "入れたよ：${PLIST}（ログ ${LOG}）"
  ;;
uninstall)
  ST=$(job_state)
  if [ "$ST" = unknown ]; then
    echo "自動起動の状態を調べられなかったので、外すのをやめたよ。plist はそのまま：${PLIST}" >&2
    exit 1
  fi
  if [ "$ST" = loaded ] && ! launchctl bootout "gui/$(id -u)/$LABEL"; then
    echo "止められなかったよ（launchctl bootout が失敗）。plist はそのまま残した：${PLIST}" >&2
    exit 1
  fi
  if [ "$(job_state)" != absent ]; then
    echo "まだ止まっていない、または状態が分からないよ。plist はそのまま：${PLIST}" >&2
    exit 1
  fi
  if [ -f "$PLIST" ]; then
    if command -v trash >/dev/null; then trash "$PLIST"; else mv "$PLIST" "$HOME/.Trash/"; fi
  fi
  if [ -f "$PLIST" ]; then
    echo "plist をゴミ箱に移せなかったよ：${PLIST}" >&2
    exit 1
  fi
  echo "外したよ（plist はゴミ箱）"
  ;;
status)
  ST=$(job_state)
  if [ "$ST" = loaded ]; then
    echo "入っているよ：${PLIST}"
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "^[[:space:]]*(state|pid) =" | head -2
    if [ "$(curl -s --max-time 2 "http://127.0.0.1:8765/app-id" 2>/dev/null)" = "keiba-confirm-app" ]; then
      echo "応答あり：http://127.0.0.1:8765/"
    else
      echo "まだ応答が無い、または別のものが応答しているよ（ログ ${LOG}）"
    fi
  elif [ "$ST" = absent ]; then
    echo "入っていないよ（自動起動は未登録）"
    exit 3
  else
    echo "状態を調べられなかったよ（launchctl print の失敗）" >&2
    exit 4
  fi
  ;;
*) echo "使い方: sh ${0} install|uninstall|status"; exit 2 ;;
esac
