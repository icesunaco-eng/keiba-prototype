"""💚20261124-2 直してから①：発走時刻が変わったあとも、画面の時点の判定は正式の判定と同じ
「予想を保存した時点で分かっていた発走時刻」で行う。"""
from datetime import datetime, timezone

import pytest

from keiba import official
from keiba.app.checks import material_timing, pre_publish_check
from keiba.app.data import Source
from keiba.config import Paths
from keiba.ledger import Ledger
from keiba.live.collector import Collector
from tests.test_app import _pred
from tests.test_live_wallet import card_html

DAY = "2026-11-21"
KEY = f"{DAY}|京都|1"
T = lambda hm: datetime.fromisoformat(f"{DAY}T{hm}:00+09:00")


@pytest.mark.parametrize("post,known_at", [("10:00", None), ("09:56", "09:47"), ("10:20", "09:48")])
def test_screen_timing_uses_post_known_at_save(tmp_path, post, known_at):
    paths = Paths(data_dir=tmp_path / "data")
    col = Collector(paths.data_dir / "live", now=lambda: T("08:00"))
    col.state["races"][KEY] = {"race_date": DAY, "venue": "京都", "race_number": 1, "post_time": "10:00", "field_size": 4}
    col.record_post_times()
    col._write_state()
    col.now = lambda: T("09:43")
    col._save(KEY, "card", "15m", "/card", "fixture", card_html([f"{i:010}" for i in range(1, 5)]))
    col.now = lambda: T("09:44")
    col._save(KEY, "odds_unavailable", "15m", "/trio", "fixture", "未発表")
    led = Ledger(paths.ledger_path)
    led._now = lambda: T("09:46").astimezone(timezone.utc).isoformat(timespec="seconds")
    led.add_prediction(KEY, "15m", "ml_lgbm", "live-fixture", _pred([1, 2, 3, 4], stakes={"1-2-3": 100}, card_at=T("09:43").isoformat()),
                       "i", "m", "c")
    led.commit()
    led.close()
    if known_at:
        col.now = lambda: T(known_at)
        col.state["races"][KEY]["post_time"] = post
        col.record_post_times()
        col._write_state()
    src = Source(paths, "live")
    p = src.preds[KEY][("ml_lgbm", "15m")]
    led = Ledger(paths.ledger_path, read_only=True)
    assert official.load(led.conn, col.root).is_counted(KEY, "15m", "ml_lgbm", "live-fixture")
    led.close()
    assert p["_known_post"] == "10:00" and src.races[KEY]["post_time"] == post
    state, text = material_timing(p, src.races[KEY], "15m")
    assert state == "ok", text
    if known_at:
        assert "10:00" in text and post in text           # どの発走時刻で判定したかを書く
    items = {i["label"]: i for i in pre_publish_check(p, {"1-2-3": 100}, src.races[KEY], None, None, None, "15m")}
    assert items["時点の材料"]["ok"] is True
    assert items["出馬表の新しさ"]["ok"] is True, items["出馬表の新しさ"]
