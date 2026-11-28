"""精算は「予想1行の3配分」をまとめて書く。途中で失敗したら1つも確定しない（台帳は追記専用で消せない）。"""
from datetime import datetime

import pytest

from keiba.ledger import Ledger
from keiba.live import jra, wallet
from keiba.live.collector import JST
from tests.test_live_wallet import RK, _setup, result_html

GOOD = {"status": "allocated", "stakes": {"1-2-3": 100}, "total": 100}
BROKEN = {
    "文字": "allocated", "空": None, "並び": [], "stakesが無い": {"status": "allocated"},
    "金額が文字": {"status": "allocated", "stakes": {"1-2-3": "100"}},
    "金額が非数": {"status": "allocated", "stakes": {"1-2-3": float("nan")}},
    "金額が無限": {"status": "allocated", "stakes": {"1-2-3": float("inf")}},
    "組が文字": {"status": "allocated", "stakes": {"a-b-c": 100}},
}
AFTER = datetime(2025, 8, 30, 16, 15, tzinfo=JST)


@pytest.fixture
def env(tmp_path, monkeypatch):
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card", lambda p: [dict(x, horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    return _setup(tmp_path)


def put(paths, kelly, method="text_ai", first="kelly"):
    led = Ledger(paths.ledger_path)
    alloc = {a: GOOD for a in wallet.ALLOC_METHODS}
    alloc[first] = kelly
    led.add_prediction(RK, "1h", method, "live-mam-v1", {"race_date": "2025-08-30", "horses": [{"n": n} for n in range(1, 9)],
                                                        "trios": [], "allocation": alloc}, "i", "m", "c")
    led.commit()
    led.close()


def finish(col, how):
    if how == "結果":
        col._save(RK, "result", "result", "/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
    else:
        (col.root / "cancellations.jsonl").write_text(
            '{"race_date": "2025-08-30", "venue": "東京", "from_race": 8, "to_race": null, "title": "取りやめ"}\n', encoding="utf-8")


def settled(paths):
    led = Ledger(paths.ledger_path, read_only=True)
    out = list(led.settlements())
    led.close()
    return out


@pytest.mark.parametrize("how", ["結果", "取りやめ"])
@pytest.mark.parametrize("kind", BROKEN)
def test_broken_allocation_settles_none_of_the_row(env, how, kind):
    paths, col = env
    put(paths, BROKEN[kind])
    put(paths, GOOD, method="zz_next")
    finish(col, how)
    out = wallet.run_live(paths, now=AFTER)
    rows = settled(paths)
    bad = [s for s in rows if s["method"] == "text_ai"]
    if how == "取りやめ" and kind == "組が文字":
        assert len(bad) in (0, 3)      # 全額返還は組を読まないので精算できる。一部だけにはしない
    else:
        assert not bad and out["failed"]
    assert len([s for s in rows if s["method"] == "zz_next"]) == 3      # 次の方式は止めない


@pytest.mark.parametrize("how", ["結果", "取りやめ"])
@pytest.mark.parametrize("nth", [1, 2, 3])
def test_failed_write_rolls_back_the_row(env, monkeypatch, how, nth):
    paths, col = env
    put(paths, GOOD)
    finish(col, how)
    orig, n = Ledger.add_settlement, {"i": 0}

    def fail(self, *a, **k):
        n["i"] += 1
        if n["i"] == nth:
            raise ValueError("書けない")
        return orig(self, *a, **k)
    with monkeypatch.context() as m:
        m.setattr(Ledger, "add_settlement", fail)
        assert wallet.run_live(paths, now=AFTER)["failed"]
    assert not settled(paths)
    wallet.run_live(paths, now=AFTER)
    assert len(settled(paths)) == 3      # 次の回にそろって入る


def test_waiting_payout_still_waits_only_that_wallet(env):
    """払戻の欠けで待つのは今までどおり配分ごと（壊れた行の巻き戻しと混ぜない）。"""
    import re
    paths, col = env
    put(paths, {"status": "zero_no_value", "stakes": {}})
    col._save(RK, "result", "result", "/x", "c", re.sub(r'<li class="trio">.*?</li>', "", result_html([3, 1, 2, 4, 5, 6, 7, 8])))
    wallet.run_live(paths, now=AFTER)
    assert [s["wallet"] for s in settled(paths)] == ["kelly"]
