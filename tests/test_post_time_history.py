"""締切は、予想を保存した時点で分かっていた発走時刻（2026-11-23 決定）。最終の発走より後の保存は数えない。"""
import json
import types
from datetime import datetime, timedelta, timezone

import pytest

from keiba import official
from keiba.ledger import Ledger
from keiba.live.collector import JST, Collector

RK = "2026-11-21|京都|1"
POST = datetime(2026, 11, 21, 10, 0, tzinfo=JST)


@pytest.fixture
def col(tmp_path):
    c = Collector(tmp_path / "live", client=types.SimpleNamespace(count=0), now=lambda: POST - timedelta(hours=26))
    c.state["races"][RK] = {"race_date": RK[:10], "venue": "京都", "race_number": 1, "post_time": "10:00", "field_size": 8}
    c._write_state()
    return c


def change(col, at, post):
    col.now = lambda: at
    col.state["races"][RK]["post_time"] = post.strftime("%H:%M")
    col.record_post_times()
    col._write_state()


def save(col, at, tp="15m"):
    led = Ledger(col.root.parent / "ledger.sqlite")
    led._now = lambda: at.astimezone(timezone.utc).isoformat(timespec="seconds")
    led.add_prediction(RK, tp, "ml_lgbm", "live-aaaaaaaa", {"trios": []}, "i", "m", "c")
    led.commit()
    led.close()


def shown(col):
    led = Ledger(col.root.parent / "ledger.sqlite", read_only=True)
    out = official.load(led.conn, col.root)
    led.close()
    return out


@pytest.mark.parametrize("shift", [-4, -1, 1, 5, 15, 30])
def test_later_change_does_not_move_the_deadline(col, shift):
    change(col, POST - timedelta(hours=26), POST)
    save(col, POST - timedelta(minutes=14))          # 材料が無いので締切の後に作った
    change(col, POST - timedelta(minutes=10), POST + timedelta(minutes=shift))
    assert shown(col).counted


def test_saved_after_final_post_is_never_counted(col):
    change(col, POST - timedelta(hours=26), POST)
    save(col, POST - timedelta(minutes=14))
    change(col, POST - timedelta(minutes=10), POST - timedelta(minutes=15))     # 実は発走（9:45）の後の保存だった
    sel = shown(col)
    assert not sel.counted and sel.late


def test_change_known_before_saving_is_used(col):
    change(col, POST - timedelta(hours=26), POST)
    change(col, POST - timedelta(minutes=40), POST + timedelta(minutes=10))
    save(col, POST - timedelta(minutes=14))          # 新しい締切（9:55）より前・材料も無い
    assert not shown(col).counted


def test_recorded_only_when_changed(col):
    change(col, POST - timedelta(hours=26), POST)
    change(col, POST - timedelta(hours=25), POST)
    change(col, POST - timedelta(hours=2), POST + timedelta(minutes=1))
    rows = (col.root / "post_times.jsonl").read_text().splitlines()
    assert [json.loads(x)["post_time"] for x in rows] == ["10:00", "10:01"]


def test_old_observations_are_filled_from_saved_cards(col):
    for at, hhmm in ((POST - timedelta(hours=24, minutes=10), "10時00分"), (POST - timedelta(minutes=70), "10時01分")):
        col.now = lambda at=at: at
        col._save(RK, "card", "24h", "/x", hhmm, f"<html><body><p>発走時刻：<strong>{hhmm}</strong></p></body></html>")
    save(col, POST - timedelta(hours=23, minutes=59, seconds=30), tp="24h")
    col.state["races"][RK]["post_time"] = "10:01"
    col._write_state()
    before = shown(col)                              # 記録の導入前：出馬表の原本から同じ答え
    col.now = lambda: POST + timedelta(hours=1)
    col.record_post_times()
    after = shown(col)
    assert before.shown == after.shown
    assert [json.loads(x)["post_time"] for x in (col.root / "post_times.jsonl").read_text().splitlines()] == ["10:00", "10:01"]


def test_tick_records_the_change(col, monkeypatch):
    monkeypatch.setattr(Collector, "refresh", lambda self, force=False: self.state["races"][RK].update(post_time="10:03"))
    monkeypatch.setattr(Collector, "check_notices", lambda self, log=print: None)
    col.tick(log=lambda *_: None)
    assert json.loads((col.root / "post_times.jsonl").read_text().splitlines()[-1])["post_time"] == "10:03"


def test_prediction_waits_when_change_cannot_be_recorded(col, monkeypatch):
    from keiba.live import collector as c
    real = c.append_lines

    def fail(path, lines):
        if path.name == "post_times.jsonl":
            raise OSError("書けない")
        real(path, lines)
    monkeypatch.setattr(c, "append_lines", fail)
    monkeypatch.setattr(Collector, "refresh", lambda self, force=False: None)
    monkeypatch.setattr(Collector, "check_notices", lambda self, log=print: None)
    col.state["live_pending"] = True
    assert col.tick(log=lambda *_: None)["errors"] == 1
    assert not col.live_pending()                    # 記録できないうちは予想を作らない
    monkeypatch.setattr(c, "append_lines", real)
    col.tick(log=lambda *_: None)
    assert col.live_pending()
