"""1レース・1方式の失敗で、ほかのレースの予想・精算を止めない。非数は台帳に書かない。"""
import types
from datetime import datetime

import numpy as np
import pytest

from keiba.ledger import Ledger
from keiba.live import jra, mam, wallet
from keiba.live import collector as c
from keiba.live.collector import JST, Collector
from tests.test_live_wallet import RK, _setup, card_html, result_html, trio_html, win_html

RK2 = "2025-08-30|東京|12"


@pytest.fixture
def two(tmp_path, monkeypatch):
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card", lambda p: [dict(x, horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    paths, col = _setup(tmp_path)
    col.state["races"][RK2] = dict(col.state["races"][RK], race_number=12)
    for kind, page in (("card", card_html([f"H{h:04d}" for h in range(8)])), ("odds_win", win_html()), ("odds_trio", trio_html())):
        col._save(RK2, kind, "1h", "/x", "c", page)
    col._write_state()
    return paths, col


def break_market(monkeypatch, value=np.nan):
    """東京11R の ml_market だけ確率を非数にする。"""
    orig = wallet.ev.predict_race

    def bad(g, sc, cal, m, *a, **k):
        out = orig(g, sc, cal, m, *a, **k)
        if m == "ml_market" and g["race_key"].iloc[0] == RK:
            out["trios"][0]["p"] = float(value)
        return out
    monkeypatch.setattr(wallet.ev, "predict_race", bad)


def saved(paths, what="predictions"):
    led = Ledger(paths.ledger_path, read_only=True)
    out = list(getattr(led, what)())
    led.close()
    return out


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_non_finite_is_not_saved_and_others_continue(two, monkeypatch, value):
    paths, _ = two
    break_market(monkeypatch, value)
    out = wallet.run_live(paths)
    got = {(p["race_key"], p["method"]) for p in saved(paths)}
    assert (RK, "ml_market") not in got and len([k for k in got if k[0] == RK]) == 10
    assert len([k for k in got if k[0] == RK2]) == 11
    assert len(out["failed"]) == 1 and RK in out["failed"][0]


@pytest.mark.parametrize("where", ["predict_race", "_integrated", "_models"])
def test_exception_in_one_race_does_not_stop_next(two, monkeypatch, where):
    paths, _ = two
    owner = wallet.ev if where == "predict_race" else wallet
    orig, n = getattr(owner, where), {"i": 0}

    def once(*a, **k):
        n["i"] += 1
        if n["i"] == 1:
            raise ValueError("壊れた入力")
        return orig(*a, **k)
    monkeypatch.setattr(owner, where, once)
    out = wallet.run_live(paths)
    assert out["failed"] and len([p for p in saved(paths) if p["race_key"] == RK2]) == 11


def test_one_bad_settlement_does_not_stop_next(two, monkeypatch):
    paths, col = two
    wallet.run_live(paths)
    for rk in (RK, RK2):
        col._save(rk, "result", "result", "/x", rk, result_html([3, 1, 2, 4, 5, 6, 7, 8]))
    orig = Ledger.add_settlement

    def bad(self, rk, *a, **k):
        if rk == RK:
            raise ValueError("精算できない")
        return orig(self, rk, *a, **k)
    monkeypatch.setattr(Ledger, "add_settlement", bad)
    out = wallet.run_live(paths)
    assert out["failed"] and {s["race_key"] for s in saved(paths, "settlements")} == {RK2}


def test_database_lock_is_still_retried_as_a_whole(two, monkeypatch):
    import sqlite3

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(Ledger, "add_prediction", locked)
    with pytest.raises(sqlite3.OperationalError):
        wallet.run_live(two[0])


def test_tick_retries_and_notifies_once(two, monkeypatch):
    from keiba import cli
    paths, col = two
    break_market(monkeypatch)
    at = datetime(2025, 8, 30, 14, 45, tzinfo=JST)
    monkeypatch.setattr(c, "Collector", lambda root: Collector(root, client=types.SimpleNamespace(count=0), now=lambda: at))
    for name in ("refresh", "check_notices"):
        monkeypatch.setattr(Collector, name, lambda self, *a, **k: None)
    monkeypatch.setattr(Collector, "due", lambda self: [])
    monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
    sent = []
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: sent.append(a))
    for _ in range(3):
        cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
    assert len(sent) == 1 and Collector(col.root, client=None).live_pending()


@pytest.mark.parametrize("where", ["export", "review", "killed"])
def test_review_is_rebuilt_after_interruption(two, monkeypatch, where):
    """精算を台帳に書いた後に止まっても、次の回に月次を作り直す（次の回の精算は0件）。"""
    from keiba import cli, review
    paths, col = two
    wallet.run_live(paths)
    col._save(RK, "result", "result", "/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
    col._write_state()
    at = datetime(2025, 8, 30, 16, 15, tzinfo=JST)
    monkeypatch.setattr(c, "Collector", lambda root: Collector(root, client=types.SimpleNamespace(count=0), now=lambda: at))
    for name in ("refresh", "check_notices"):
        monkeypatch.setattr(Collector, name, lambda self, *a, **k: None)
    monkeypatch.setattr(Collector, "due", lambda self: [])
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
    written = []
    monkeypatch.setattr(review, "write_review", lambda p, month, *a, **k: written.append(month))

    def stop(*a, **k):
        raise (KeyboardInterrupt if where == "killed" else OSError)("途中で止まった")
    with monkeypatch.context() as m:
        m.setattr(*((review, "write_review") if where == "review" else (mam, "export_packets")), stop)
        try:
            cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        except KeyboardInterrupt:
            pass
    assert saved(paths, "settlements") and not written
    cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
    assert written
    state = Collector(col.root, client=None)
    assert not state.live_pending() and not state.state.get("live_running")   # 正常に終わったら印が消える
