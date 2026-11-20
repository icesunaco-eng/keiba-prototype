"""本人の購入記録とメモ（追記専用の SQLite。予想の台帳とは別のファイル）。

実際のお金の記録なので、模擬（仮想の財布）とは混ぜない。書き換え・削除はしない：
  - 直すときは同じ entry_id で rev を1つ上げた行を足す（一番新しい rev が今の内容）
  - 取り消すときは kind="void" の行を足す
UPDATE と DELETE はトリガーで止める。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..settle import settle_trio

UNIT = 100
MAX_PER_COMBO = 1_000_000
KINDS = ("buy", "skip", "void")

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT NOT NULL, rev INTEGER NOT NULL,
  created_at TEXT NOT NULL, race_key TEXT NOT NULL, method TEXT, time_point TEXT,
  kind TEXT NOT NULL, stakes TEXT NOT NULL, memo TEXT NOT NULL, data_mode TEXT NOT NULL,
  UNIQUE (entry_id, rev));
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, race_key TEXT NOT NULL, created_at TEXT NOT NULL, text TEXT NOT NULL);
"""
TRIGGERS = [f"""CREATE TRIGGER IF NOT EXISTS no_{op}_{t} BEFORE {op.upper()} ON {t}
BEGIN SELECT RAISE(ABORT, 'append-only'); END;""" for t in ("purchases", "notes") for op in ("update", "delete")]


class InputError(ValueError):
    """入力が正しくない（画面にそのまま出す文）。"""


def normalize_combo(text: str) -> str:
    """'7-3-11' や '3,7,11' → '3-7-11'（三連複なので順番は関係ない）。"""
    parts = [p for p in text.replace(",", "-").replace("－", "-").replace(" ", "-").split("-") if p]
    try:
        nums = sorted(int(p) for p in parts)
    except ValueError:
        raise InputError(f"組「{text}」は馬番3つを - でつないでね") from None
    if len(nums) != 3 or len(set(nums)) != 3 or not all(1 <= n <= 18 for n in nums):
        raise InputError(f"組「{text}」は、ちがう馬番（1〜18）を3つにしてね")
    return "-".join(map(str, nums))


def clean_stakes(raw) -> dict:
    """{組: 円} か [(組, 円), …]。同じ組が2回あれば足す。"""
    out: dict[str, int] = {}
    for combo, yen in (raw.items() if isinstance(raw, dict) else raw):
        if yen in (None, "", 0, "0"):
            continue
        try:
            v = int(str(yen).replace(",", ""))
        except ValueError:
            raise InputError(f"{combo} の金額は数字で入れてね") from None
        if v < 0 or v % UNIT or v > MAX_PER_COMBO:
            raise InputError(f"{combo} の金額は100円単位（0〜{MAX_PER_COMBO:,}円）にしてね")
        c = normalize_combo(combo)
        out[c] = out.get(c, 0) + v
    return {k: v for k, v in sorted(out.items()) if v}


class Store:
    def __init__(self, path: Path, protect: tuple = ()):
        """protect：書いてはいけないファイル（予想の台帳など）。同じファイルを指していたら開かない。"""
        self.path = Path(path)
        for p in protect:
            if p and Path(p).exists() and self.path.exists() and self.path.resolve().samefile(Path(p).resolve()):
                raise ValueError(f"購入記録の保存先が予想の台帳と同じファイル：{self.path}")
            if p and self.path.resolve() == Path(p).resolve():
                raise ValueError(f"購入記録の保存先が予想の台帳と同じ場所：{self.path}")
        if self.path.exists():
            c = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                c.close()
            if tables & {"predictions", "settlements", "runs"}:
                raise ValueError(f"このファイルは予想の台帳なので、購入記録には使えない：{self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.conn.executescript(SCHEMA)
        for t in TRIGGERS:
            self.conn.execute(t)
        self.conn.commit()

    def close(self):
        self.conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add(self, race_key: str, kind: str, stakes: dict, memo: str = "", method: str | None = None,
            time_point: str | None = None, data_mode: str = "live", entry_id: str | None = None) -> str:
        if kind not in KINDS:
            raise InputError("種類が正しくない")
        stakes = clean_stakes(stakes) if kind == "buy" else {}
        if kind == "buy" and not stakes:
            raise InputError("買った組と金額を1つ以上入れてね")
        memo = (memo or "").strip()[:1000]
        new = not entry_id
        entry_id = entry_id or uuid.uuid4().hex[:12]
        with self._lock:   # 同じアプリの中では1つずつ
            for attempt in range(20):   # 別のアプリ（別の接続）と rev がぶつかったら、読み直してやり直す
                rev, mode = 1, data_mode
                if not new:
                    last = self.conn.execute("SELECT MAX(rev), MIN(race_key) FROM purchases WHERE entry_id=?", (entry_id,)).fetchone()
                    if not last or last[0] is None:
                        raise InputError("直す記録が見つからない")
                    if last[1] != race_key:
                        raise InputError("別のレースの記録は直せない")
                    rev = last[0] + 1
                    # 直しても、最初（rev 1）に記録したときの見方のまま。旧版で途中の rev が変わっていても最初に戻す
                    mode = self.conn.execute("SELECT data_mode FROM purchases WHERE entry_id=? ORDER BY rev LIMIT 1",
                                             (entry_id,)).fetchone()[0]
                try:
                    self.conn.execute("INSERT INTO purchases (entry_id,rev,created_at,race_key,method,time_point,kind,stakes,memo,data_mode)"
                                      " VALUES (?,?,?,?,?,?,?,?,?,?)",
                                      (entry_id, rev, self._now(), race_key, method, time_point, kind,
                                       json.dumps(stakes, ensure_ascii=False, sort_keys=True), memo, mode))
                    self.conn.commit()
                    return entry_id
                except sqlite3.IntegrityError:
                    self.conn.rollback()
                    if new:
                        entry_id = uuid.uuid4().hex[:12]
            raise InputError("同時に直されていて保存できなかったよ。もう一度保存してね")

    def entries(self, race_key: str | None = None, data_mode: str | None = None, include_void: bool = False) -> list[dict]:
        """各記録の一番新しい rev だけ（新しい順）。"""
        first_mode = "(SELECT f.data_mode FROM purchases f WHERE f.entry_id=p.entry_id ORDER BY f.rev LIMIT 1)"
        q = (f"SELECT p.entry_id,p.rev,p.created_at,p.race_key,p.method,p.time_point,p.kind,p.stakes,p.memo,{first_mode},"
             "(SELECT MIN(created_at) FROM purchases f WHERE f.entry_id=p.entry_id),"
             "(SELECT COUNT(DISTINCT data_mode) FROM purchases f WHERE f.entry_id=p.entry_id) FROM purchases p "
             "WHERE p.rev=(SELECT MAX(rev) FROM purchases q WHERE q.entry_id=p.entry_id)")
        args: list = []
        if race_key:
            q += " AND p.race_key=?"
            args.append(race_key)
        if data_mode:   # 見方は最初（rev 1）の記録で決める
            q += f" AND {first_mode}=?"
            args.append(data_mode)
        q += " ORDER BY p.id DESC"
        out = []
        for r in self.conn.execute(q, args):
            e = {"entry_id": r[0], "rev": r[1], "updated_at": r[2], "race_key": r[3], "method": r[4], "time_point": r[5],
                 "kind": r[6], "stakes": json.loads(r[7]), "memo": r[8], "data_mode": r[9], "created_at": r[10],
                 "mode_mixed": r[11] > 1}
            e["total"] = sum(e["stakes"].values())
            if include_void or e["kind"] != "void":
                out.append(e)
        return out

    def entry(self, entry_id: str) -> dict | None:
        return next((e for e in self.entries(include_void=True) if e["entry_id"] == entry_id), None)

    def history(self, entry_id: str) -> list[dict]:
        return [{"rev": r[0], "created_at": r[1], "kind": r[2], "stakes": json.loads(r[3]), "memo": r[4]}
                for r in self.conn.execute("SELECT rev,created_at,kind,stakes,memo FROM purchases WHERE entry_id=? ORDER BY rev",
                                           (entry_id,))]

    def set_note(self, race_key: str, text: str):
        self.conn.execute("INSERT INTO notes (race_key,created_at,text) VALUES (?,?,?)",
                          (race_key, self._now(), (text or "").strip()[:2000]))
        self.conn.commit()

    def note(self, race_key: str) -> str:
        row = self.conn.execute("SELECT text FROM notes WHERE race_key=? ORDER BY id DESC LIMIT 1", (race_key,)).fetchone()
        return row[0] if row else ""

    def noted_races(self) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT race_key FROM notes n WHERE id=(SELECT MAX(id) FROM notes m WHERE m.race_key=n.race_key) AND text<>''"
            " AND race_key NOT LIKE 'improve|%'")}


SETTLE_LABEL = {"pending": "結果待ち", "partial_result": "保留（結果を一部しか読めていない）",
                "unsettled_no_payout": "払戻待ち（払戻がまだ分からない）",
                "refunded_void": "返還（レース不成立）", "settled": "精算ずみ"}


def settle_purchase(entry: dict, res: dict | None) -> dict:
    """本人の購入記録を、予想の精算と同じ処理（settle.settle_trio）で精算する。

    同着（的中組が複数）・取消／除外の返還・不成立（完走3頭未満）を扱う。払戻が分からないときは
    「払戻待ち」にして、金額と回収率の計算から外す（0円にはしない）。
    """
    stake = entry["total"]
    if entry["kind"] != "buy":
        return {"status": entry["kind"], "stake": 0, "returned": 0, "hit": None, "refund": 0, "counted": False}
    res = res or {}
    if not res.get("has_result"):
        return {"status": "pending", "stake": stake, "returned": 0, "hit": None, "refund": 0, "counted": False}
    payouts = res.get("payouts") or None
    scratched = {int(n) for n in res.get("scratched") or ()}
    # 取消・除外の馬が入った組の返還は、結果の残りが分からなくても確定している（保留中も金額を出す。集計には入れない）
    sure_refund = sum(v for c, v in entry["stakes"].items() if {int(x) for x in c.split("-")} & scratched)
    if res.get("complete") is False:
        # 結果を一部しか読めていない：不成立（全額返還）とは決めず、保留
        return {"status": "partial_result", "stake": stake, "returned": sure_refund, "hit": None,
                "refund": sure_refund, "counted": False, "reason": res.get("incomplete")}
    live = {c for c in entry["stakes"] if not ({int(x) for x in c.split("-")} & scratched)}
    missing = set(res.get("payouts_missing") or ()) & live
    if missing:
        # 同着で的中組の払戻が一部欠けている：その組を買っているなら、0円にせず払戻待ち（確定した返還は残す）
        return {"status": "unsettled_no_payout", "stake": stake, "returned": sure_refund, "hit": "・".join(sorted(missing)),
                "refund": sure_refund, "counted": False}
    st = settle_trio(entry["stakes"], int(res.get("finishers") or 0), scratched, payouts)
    refund = sum(entry["stakes"][c] for c in st.refunds) if st.status != "refunded_void" else stake
    return {"status": st.status, "stake": stake, "returned": st.returned, "hit": "・".join(st.hits) or None,
            "refund": refund, "counted": st.status in ("settled", "refunded_void")}
