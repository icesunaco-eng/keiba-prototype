"""追記専用の台帳（SQLite 1ファイル）。

鍵は「レース × 時点 × 方式 × 版」。同じ鍵で同じ中身（ハッシュ一致）を入れ直すのは何もしない。
同じ鍵で違う中身は拒否する（予想を後から書き換えない）。UPDATE と DELETE はトリガーで止める。
各行に入力・モデル・コードのハッシュを持たせる。読むだけのときは読取専用で開く。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  race_key TEXT NOT NULL, time_point TEXT NOT NULL, method TEXT NOT NULL, version TEXT NOT NULL,
  created_at TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
  input_hash TEXT NOT NULL, model_hash TEXT NOT NULL, code_hash TEXT NOT NULL,
  PRIMARY KEY (race_key, time_point, method, version));
CREATE TABLE IF NOT EXISTS settlements (
  race_key TEXT NOT NULL, time_point TEXT NOT NULL, method TEXT NOT NULL, version TEXT NOT NULL,
  wallet TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
  PRIMARY KEY (race_key, time_point, method, version, wallet));
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL);
"""
TRIGGERS = [f"""CREATE TRIGGER IF NOT EXISTS no_{op}_{t} BEFORE {op.upper()} ON {t}
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;""" for t in ("predictions", "settlements", "runs")
            for op in ("update", "delete")]


class LedgerConflict(Exception):
    """同じ鍵で違う中身を入れようとした。"""


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def code_hash() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


class Ledger:
    def __init__(self, path: Path, read_only: bool = False):
        self.path = Path(path)
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path)
            self.conn.executescript(SCHEMA)
            for t in TRIGGERS:
                self.conn.execute(t)
            self.conn.commit()

    def close(self):
        self.conn.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add_prediction(self, race_key, time_point, method, version, payload, input_hash, model_hash, code):
        text = canonical(payload)
        h = sha(text)
        row = self.conn.execute(
            "SELECT payload_hash FROM predictions WHERE race_key=? AND time_point=? AND method=? AND version=?",
            (race_key, time_point, method, version)).fetchone()
        if row:
            if row[0] != h:
                raise LedgerConflict(f"{race_key} {time_point} {method} {version}: 中身が違う")
            return False
        self.conn.execute("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (race_key, time_point, method, version, self._now(), text, h, input_hash, model_hash, code))
        return True

    def add_settlement(self, race_key, time_point, method, version, wallet, payload):
        text = canonical(payload)
        h = sha(text)
        row = self.conn.execute(
            "SELECT payload_hash FROM settlements WHERE race_key=? AND time_point=? AND method=? AND version=? AND wallet=?",
            (race_key, time_point, method, version, wallet)).fetchone()
        if row:
            if row[0] != h:
                raise LedgerConflict(f"{race_key} {time_point} {method} {version} {wallet}: 精算の中身が違う")
            return False
        self.conn.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?)",
                          (race_key, time_point, method, version, wallet, self._now(), text, h))
        return True

    def add_run(self, run_id: str, payload: dict):
        if not self.conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
            self.conn.execute("INSERT INTO runs VALUES (?,?,?)", (run_id, self._now(), canonical(payload)))

    def commit(self):
        self.conn.commit()

    def predictions(self, version: str | None = None, live: bool = False):
        q = "SELECT race_key,time_point,method,version,payload FROM predictions"
        args = ()
        if version:
            q += " WHERE version=?"
            args = (version,)
        elif live:
            q += " WHERE version LIKE 'live-%'"
        for rk, tp, m, v, p in self.conn.execute(q, args):
            yield {"race_key": rk, "time_point": tp, "method": m, "version": v, **json.loads(p)}

    def settlements(self, version: str | None = None):
        q = "SELECT race_key,time_point,method,version,wallet,payload FROM settlements"
        args = ()
        if version:
            q += " WHERE version=?"
            args = (version,)
        for rk, tp, m, v, w, p in self.conn.execute(q, args):
            yield {"race_key": rk, "time_point": tp, "method": m, "version": v, "wallet": w, **json.loads(p)}

    def verify(self) -> dict:
        """保存された中身がハッシュと一致するか確かめる。"""
        bad = [r[:4] for r in self.conn.execute("SELECT race_key,time_point,method,version,payload,payload_hash FROM predictions")
               if sha(r[4]) != r[5]]
        n = self.conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        return {"predictions": n, "hash_mismatch": len(bad)}
