#!/bin/bash
# 使い方: run-all.sh <ラベル>  — この隔離先の中だけで自己試験を実行する。既存のログは消さない（同名ラベルは拒否）。
set -u
N="$(cd "$(dirname "$0")/.." && pwd)"
L="$N/logs/$1"; test ! -e "$L" || { echo "既存ラベル: $L"; exit 2; }; mkdir -p "$L"
PY="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
date -u +%FT%TZ > "$L/started_utc.txt"
shasum -a 256 "$N/cases/cases.json" > "$L/cases_sha256.txt"
/usr/bin/env -i HOME="$N/home" PATH="/usr/bin:/bin" LANG=en_US.UTF-8 AUDIT_NAME="$1/audit.json" \
  /usr/bin/sandbox-exec -f "$N/tools/no-network.sb" "$PY" -I -B "$N/tools/guard.py" "$N/tools/run_cases.py" "$N/cases/cases.json" "$L/results.json" > "$L/run.log" 2>&1
echo "exit $?" > "$L/exit.txt"
date -u +%FT%TZ > "$L/ended_utc.txt"
"$PY" -V > "$L/python.txt" 2>&1; sw_vers > "$L/os.txt" 2>&1
tail -1 "$L/run.log"; cat "$L/exit.txt"
