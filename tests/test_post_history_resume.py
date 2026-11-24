"""💚20261124-2 止める③：初回の発走履歴の穴埋めが途中（2行目の途中）で止まっても、再起動で欠けた観測を埋め、
発走時刻・期限・既存の成績を変えない。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from keiba import official
from keiba.config import Paths
from keiba.ledger import Ledger
from keiba.live import collector, wallet
from keiba.live.collector import Collector
from keiba.review import build_review
from tests.test_live_wallet import card_html, result_html, trio_html

DAY = "2026-11-22"
RK = f"{DAY}|東京|3"
POST = datetime.fromisoformat(f"{DAY}T15:40:00+09:00")
M = "ml_lgbm"


def _ts(t):
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _card(label):
    return f"<html><body><p>発走時刻：<strong>{label}</strong></p>{card_html(range(8))}</body></html>"


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize("cut", [0, 18, "whole"])
def test_interrupted_fill_keeps_history_and_results(tmp_path, cut):
    paths = Paths(data_dir=tmp_path)
    root = tmp_path / "live"
    now = POST + timedelta(days=2)
    col = Collector(root, client=SimpleNamespace(count=0), now=lambda: now)
    col.state["races"][RK] = {"race_date": DAY, "venue": "東京", "race_number": 3, "post_time": "15:41",
                             "field_size": 8, "surface": "芝", "distance_m": 1600}

    def save(at, kind, tp, html):
        col.now = lambda: at
        col._save(RK, kind, tp, "/x", "synth", html)
    save(POST - timedelta(days=1, minutes=10), "card", "24h", _card("15時40分"))
    save(POST - timedelta(minutes=70), "card", "1h", _card("15時41分"))
    save(POST - timedelta(minutes=69), "odds_trio", "1h", trio_html(time="14時30分"))
    save(POST + timedelta(minutes=31), "result", "result", result_html(list(range(1, 9))))
    col._write_state()
    col.now = lambda: now
    led = Ledger(paths.ledger_path)
    led._now = lambda: _ts(POST - timedelta(minutes=49))     # 14:51。発走15:41を知っていた（締切14:41＋10分ちょうど）
    led.add_prediction(RK, "1h", M, "live-old", {"race_date": DAY, "period": "live", "horses": [],
                                                 "trios": [{"combo": "1-2-3", "p": .2}],
                                                 "allocation": {a: {"status": "allocated", "stakes": {"1-2-3": 200}, "total": 200}
                                                                for a in wallet.ALLOC_METHODS}}, "i", "m", "c")
    for a in wallet.ALLOC_METHODS:
        led.add_settlement(RK, "1h", M, "live-old", a, {"status": "settled", "stake": 200, "returned": 9120, "hits": ["1-2-3"]})
    led.commit()
    assert official.load(led.conn, root).is_counted(RK, "1h", M, "live-old")
    before = official.post_history(root, {RK})[RK]

    def cut_append(path, lines):
        assert path.name == collector.POST_FILE and len(lines) == 2
        path.write_text("" if cut == "whole" else lines[0] + "\n" + lines[1][:cut])
        raise Interrupted()
    with pytest.raises(Interrupted), patch.object(collector, "append_lines", cut_append):
        col.record_post_times()
    resumed = Collector(root, client=SimpleNamespace(count=0), now=lambda: now)
    assert "post_history_filled" not in resumed.state
    resumed.record_post_times()
    assert official.post_history(root, {RK})[RK][:2] == before[:2]
    assert official.load(led.conn, root).is_counted(RK, "1h", M, "live-old")
    resumed.record_post_times()                                   # もう一度動いても行を増やさない
    rows = (root / collector.POST_FILE).read_text().splitlines()
    assert sum(1 for x in rows if '"15:41"' in x and "T14:30" in x) == 1
    led.close()
    s = wallet.wallet_summary(paths)
    assert not s.empty and int(s["stake"].sum()) == 200 * len(wallet.ALLOC_METHODS)
    assert build_review(paths, DAY[:7])["wallets"]
