"""これからのレースの予想のうち、どれを表示・精算・集計に使うか（2026-11-22 たかむね決定）。

「その時点の材料を取った後、締切＋10分までに最初に保存した版」だけが正式。台帳の行は消さず、読むときに選ぶ。
  - 下限：その時点の材料（出馬表 → 三連複オッズか「オッズ未発表」の記録）が締切までにそろった時刻。そろわなければ締切
  - 期限：その時点の締切（24h・1h・15m）＋10分。どちらも台帳に書いた時刻（created_at）で見る。発走時刻が分からなければ使わない
  - 表示：レース × 方式 × 時点ごとに、材料の後、期限までに最初に保存した版
  - 集計・精算：レース × 方式ごとに1つ。15分前（無ければ1時間前）の正式な版
発走時刻が変わったとき（2026-11-23 たかむね決定）：
  - 締切は、予想を保存した時点で分かっていた発走時刻で決める。後から動かさない（記録は post_times.jsonl、追記だけ）
  - ただし、最終の発走時刻より後に保存したものは、どの場合も数えない
成績は見ない（時刻だけで選ぶ）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRACE_MINUTES = 10   # 締切のあと、ここまでに保存したものだけが正式
MATERIAL_ODDS = ("odds_trio", "odds_unavailable")   # 1つの時点の取得で最後に残る記録
COUNT_TIME_POINTS = ("15m", "1h")   # 精算に使う時点（先にある方）。24時間前の予想は精算しない
_HHMM = re.compile(r"\d{2}:\d{2}")


@dataclass
class Selection:
    valid: set = field(default_factory=set)      # 材料の後・期限までに保存した (レース, 時点, 方式, 版)
    early: set = field(default_factory=set)      # 材料を取る前に保存した
    late: set = field(default_factory=set)       # 期限より後に保存した
    unknown: set = field(default_factory=set)    # 発走時刻が分からない
    shown: dict = field(default_factory=dict)    # (レース, 方式, 時点) → 版
    counted: dict = field(default_factory=dict)  # (レース, 方式) → (時点, 版)
    known: dict = field(default_factory=dict)    # (レース, 時点, 方式, 版) → 保存した時点で分かっていた発走時刻（判定に使ったもの）

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


def material_time(records, dl: datetime) -> datetime | None:
    """その時点の材料がそろった時刻。records：その時点の（種類, 取得時刻）。取得の時間帯の中でそろわなければ None。
    予想を作ってよいか（wallet.input_ready）と、正式に数えるかの両方がこの関数を使う。"""
    from .live.collector import CAPTURE_LEAD_MIN

    # 取得の時間帯（締切の15分前〜締切）の材料だけ。時間帯より前に取ったもの（発走時刻が後で変わったときなど）は早めの参考
    opens = dl - timedelta(minutes=CAPTURE_LEAD_MIN)
    got = [(k, t) for k, t in ((k, _moment(at)) for k, at in records) if t and opens <= t <= dl]
    cards = [t for k, t in got if k == "card"]
    if not cards:
        return None
    start = min(cards)
    return min((t for k, t in got if k in MATERIAL_ODDS and t >= start), default=None)


def card_observations(root: Path, keys) -> dict[str, list]:
    """発走時刻の記録が無いレースの（分かった時刻, 発走時刻）を、保存した出馬表から読む（取得した順）。"""
    from .app.data import card_post_time
    from .live.wallet import _read, _snapshots

    out: dict[str, list] = {}
    if not keys or not (root / "snapshots.jsonl").exists():
        return out
    try:
        snaps = _snapshots(root)
        cards = snaps[(snaps["kind"] == "card") & snaps["race_key"].isin(list(keys))]
        for _, rec in cards.iterrows():
            at = _moment(rec.get("fetched_at"))
            if not at:
                continue
            try:
                post = card_post_time(_read(root, rec))
            except (OSError, ValueError, TypeError):
                continue
            if post and _HHMM.fullmatch(post):
                out.setdefault(rec["race_key"], []).append((at, post))
    except (OSError, KeyError):
        return out
    for seen in out.values():
        seen.sort(key=lambda x: x[0])
    return out


def recorded_posts(root: Path) -> dict[str, list]:
    """収集が残した発走時刻の記録：レース → （分かった時刻, 発走時刻）の並び。"""
    from .live.collector import POST_FILE, read_jsonl

    out: dict[str, list] = {}
    try:
        rows = read_jsonl(root / POST_FILE)[0]
    except OSError:
        return out
    for x in rows:
        rk, post, at = x.get("race_key"), x.get("post_time"), _moment(x.get("seen_at"))
        if isinstance(rk, str) and isinstance(post, str) and _HHMM.fullmatch(post) and at:
            out.setdefault(rk, []).append((at, post))
    for seen in out.values():
        seen.sort(key=lambda x: x[0])
    return out


def post_history(root: Path, keys) -> dict[str, list]:
    out = recorded_posts(root)
    out.update(card_observations(root, set(keys) - set(out)))   # 記録を始める前のレースは原本から
    return out


def known_post(seen: list | None, at: datetime, final: str | None) -> str | None:
    """その時刻に分かっていた発走時刻。それより前の記録が無ければ最初の記録、記録が無ければ最終の発走時刻。"""
    if not seen:
        return final
    out = seen[0][1]
    for when, post in seen:
        if when > at:
            break
        out = post
    return out


def capture_records(root: Path) -> dict[tuple, list]:
    """(レース, 時点) → （種類, 取得時刻）の並び。目録が読めなければ空（＝材料は締切までそろわなかった扱い）。"""
    from .live.wallet import _snapshots

    out: dict[tuple, list] = {}
    if not (root / "snapshots.jsonl").exists():
        return out
    try:
        snaps = _snapshots(root)
        for rk, tp, kind, at in snaps[["race_key", "time_point", "kind", "fetched_at"]].itertuples(index=False):
            if isinstance(rk, str) and isinstance(tp, str):
                out.setdefault((rk, tp), []).append((kind, at))
    except (OSError, KeyError):
        return {}
    return out


def select(rows: list[tuple], posts: dict[str, str], records: dict[tuple, list],
           history: dict[str, list] | None = None) -> Selection:
    from .live.collector import race_post

    sel, first, start = Selection(), {}, {}
    for i, (rk, tp, m, v, created) in enumerate(rows):
        at, final = _moment(created), posts.get(rk)
        known = known_post((history or {}).get(rk), at, final) if at and final else None
        limit, ended = save_limit(rk, tp, known), race_post(rk, {"post_time": final})
        if known:
            sel.known[(rk, tp, m, v)] = known
        if not limit or not at or not ended:
            sel.unknown.add((rk, tp, m, v))
            continue
        if (rk, tp, known) not in start:
            dl = limit - timedelta(minutes=GRACE_MINUTES)
            # 台帳の時刻は秒まで。材料の時刻も秒にそろえる
            start[(rk, tp, known)] = (material_time(records.get((rk, tp), []), dl) or dl).replace(microsecond=0)
        if at > limit or at >= ended:
            sel.late.add((rk, tp, m, v))
        elif at < start[(rk, tp, known)]:
            sel.early.add((rk, tp, m, v))
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
    if not rows:
        return select(rows, {}, {})
    keys = {r[0] for r in rows}
    return select(rows, post_times(root, keys), capture_records(root), post_history(root, keys))
