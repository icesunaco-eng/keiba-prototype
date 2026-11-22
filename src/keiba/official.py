"""これからのレースの予想のうち、どれを表示・精算・集計に使うか（2026-11-22 たかむね決定）。

「締切＋10分までに最初に保存した版」だけが正式。台帳の行は消さず、読むときに選ぶ。
  - 期限：その時点の締切（24h・1h・15m）＋10分。台帳に書いた時刻（created_at）で見る。発走時刻が分からなければ使わない
  - 表示：レース × 方式 × 時点ごとに、期限までに最初に保存した版
  - 集計・精算：レース × 方式ごとに1つ。15分前（無ければ1時間前）の正式な版
成績は見ない（時刻だけで選ぶ）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRACE_MINUTES = 10   # 締切のあと、ここまでに保存したものだけが正式
COUNT_TIME_POINTS = ("15m", "1h")   # 精算に使う時点（先にある方）。24時間前の予想は精算しない
_HHMM = re.compile(r"\d{2}:\d{2}")


@dataclass
class Selection:
    valid: set = field(default_factory=set)      # 期限までに保存した (レース, 時点, 方式, 版)
    late: set = field(default_factory=set)       # 期限より後に保存した
    unknown: set = field(default_factory=set)    # 発走時刻が分からない
    shown: dict = field(default_factory=dict)    # (レース, 方式, 時点) → 版
    counted: dict = field(default_factory=dict)  # (レース, 方式) → (時点, 版)

    def is_shown(self, rk, tp, m, v) -> bool:
        return self.shown.get((rk, m, tp)) == v

    def is_counted(self, rk, tp, m, v) -> bool:
        return self.counted.get((rk, m)) == (tp, v)


def is_live(method: str, version: str) -> bool:
    return version.startswith("live-") or method == "text_ai"


def _moment(text) -> datetime | None:
    try:
        t = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)   # 台帳の時刻は UTC


def prediction_rows(conn) -> list[tuple]:
    """台帳に書いた順の (レース, 時点, 方式, 版, 作った時刻)。"""
    return [r for r in conn.execute("SELECT race_key,time_point,method,version,created_at FROM predictions ORDER BY rowid")
            if is_live(r[2], r[3])]


def post_times(root: Path, keys) -> dict[str, str]:
    """発走時刻。予定表 → 収集が残した過去の記録 → 出馬表の原本、の順に探す。"""
    from .live.collector import ARCHIVE_FILE, read_jsonl

    found: dict[str, str] = {}

    def put(rk, value):
        if isinstance(rk, str) and isinstance(value, str) and _HHMM.fullmatch(value):
            found[rk] = value

    try:
        for x in read_jsonl(root / ARCHIVE_FILE)[0]:
            put(x.get("race_key"), x.get("post_time"))
    except OSError:
        pass   # 読めなければ原本から探す
    try:
        for rk, r in json.loads((root / "state.json").read_text(encoding="utf-8")).get("races", {}).items():
            put(rk, r.get("post_time"))
    except (OSError, ValueError):
        pass
    missing = set(keys) - set(found)
    if missing and (root / "snapshots.jsonl").exists():
        from .app.data import card_post_time
        from .live.wallet import _read, _snapshots
        try:
            snaps = _snapshots(root)
            for rk in missing:
                cards = snaps[(snaps["race_key"] == rk) & (snaps["kind"] == "card")]
                if not len(cards):
                    continue
                try:
                    put(rk, card_post_time(_read(root, cards.iloc[-1])))
                except (OSError, ValueError, TypeError):
                    continue
        except (OSError, KeyError):
            pass
    return found


def save_limit(rk: str, tp: str, post_time: str | None) -> datetime | None:
    """正式になる期限（締切＋10分）。発走時刻が分からなければ None。"""
    from .live.collector import DEADLINE_MIN, race_post

    post = race_post(rk, {"post_time": post_time}) if post_time and tp in DEADLINE_MIN else None
    return post - timedelta(minutes=DEADLINE_MIN[tp] - GRACE_MINUTES) if post else None


def select(rows: list[tuple], posts: dict[str, str]) -> Selection:
    sel, first = Selection(), {}
    for i, (rk, tp, m, v, created) in enumerate(rows):
        limit, at = save_limit(rk, tp, posts.get(rk)), _moment(created)
        if not limit or not at:
            sel.unknown.add((rk, tp, m, v))
        elif at > limit:
            sel.late.add((rk, tp, m, v))
        else:
            sel.valid.add((rk, tp, m, v))
            if (rk, m, tp) not in first or (at, i) < first[(rk, m, tp)][0]:   # 同じ秒なら、先に書いた行
                first[(rk, m, tp)] = ((at, i), v)
    sel.shown = {k: v for k, (_, v) in first.items()}
    for rk, m, _ in sel.shown:
        for tp in COUNT_TIME_POINTS:
            if (rk, m, tp) in sel.shown:
                sel.counted[(rk, m)] = (tp, sel.shown[(rk, m, tp)])
                break
    return sel


def load(conn, root: Path) -> Selection:
    rows = prediction_rows(conn)
    return select(rows, post_times(root, {r[0] for r in rows}))
