"""失敗が続いても、別の失敗は知らせる。直らない精算の失敗で重い処理を5分おきに回し続けない。"""
import types
from datetime import datetime, timedelta

import pytest

from keiba import cli
from keiba.live import collector as c, mam, wallet
from keiba.live.collector import JST, Collector

START = datetime(2025, 8, 30, 16, 15, tzinfo=JST)
SETTLE = {"failed": ["東京11R|text_ai の精算：TypeError"], "retry_now": False}
PREDICT = {"failed": ["東京11R|15m|ml_market の予想：ValueError"], "retry_now": True}
OK = {"settled": 0}


@pytest.fixture
def play(tmp_path, monkeypatch):
    paths = types.SimpleNamespace(data_dir=tmp_path)
    sent, runs = [], []
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
    from keiba import review
    monkeypatch.setattr(review, "write_review", lambda *a, **k: None)
    real = Collector

    def step(minute, outcome, new_page=False):
        at = START + timedelta(minutes=minute)

        def tick(self, log=print, notify=None):
            if new_page:
                self._save("2025-08-30|東京|11", "card", "15m", "/x", "c", f"<html>{minute}</html>")
            return {}

        def run(p):
            runs.append(minute)
            if isinstance(outcome, Exception):
                raise outcome
            return dict(outcome)
        with monkeypatch.context() as m:
            m.setattr(real, "tick", tick)
            m.setattr(c, "Collector", lambda root: real(root, client=types.SimpleNamespace(count=0), now=lambda: at))
            m.setattr(wallet, "run_live", run)
            cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
    return step, sent, runs


def test_new_failure_is_notified_while_another_continues(play):
    step, sent, _ = play
    step(0, SETTLE, new_page=True)
    step(5, SETTLE)
    assert len(sent) == 1
    step(10, RuntimeError("全部止まる"), new_page=True)
    assert len(sent) == 2
    step(15, SETTLE, new_page=True)          # 前に知らせた失敗は繰り返さない
    step(20, PREDICT, new_page=True)         # 一部の失敗どうしでも、別のものは知らせる
    assert len(sent) == 3


def test_same_failure_is_notified_again_after_recovery(play):
    step, sent, _ = play
    step(0, SETTLE, new_page=True)
    step(5, OK, new_page=True)
    step(10, SETTLE, new_page=True)
    assert len(sent) == 2


def test_unfixable_settlement_backs_off(play):
    step, _, runs = play
    step(0, SETTLE, new_page=True)
    for minute in range(5, 70, 5):
        step(minute, SETTLE)
    assert runs == [0, 5, 35, 65]            # 1回はすぐ、同じ失敗なら30分おき


def test_new_page_runs_at_once_during_back_off(play):
    step, _, runs = play
    step(0, SETTLE, new_page=True)
    step(5, SETTLE)
    step(10, OK, new_page=True)
    assert runs == [0, 5, 10]


def test_prediction_failure_is_retried_every_tick(play):
    step, _, runs = play
    step(0, PREDICT, new_page=True)
    for minute in (5, 10, 15):
        step(minute, PREDICT)
    assert runs == [0, 5, 10, 15]            # 締切＋10分に間に合うように待たない
