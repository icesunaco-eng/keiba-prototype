"""💚20261124-2 止める②：見回りの途中で一覧の発走時刻が変わったとき、取得の時間帯を決め直す。

発走15:40の予定で14:25に見回り。取り直した一覧では15:41（1時間前の取得の時間帯は14:26〜14:41）。
14:25には取らず、14:30（3番の取消が出た後）に取る。早めに取ったものは正式の材料にしない。"""
from datetime import datetime, timedelta, timezone
from itertools import combinations
from unittest.mock import patch

import pandas as pd

from keiba import official
from keiba.config import Paths
from keiba.ledger import Ledger
from keiba.live import wallet
from keiba.live.collector import Collector
from tests.conftest import make_tables
from tests.test_live_collector import FakeClient, ODDS, race_list
from tests.test_live_wallet import card_html, result_html, trio_html, win_html

DAY = "2026-11-22"
RK = f"{DAY}|京都|11"
T = lambda hm: datetime.fromisoformat(f"{DAY}T{hm}:00+09:00")
ids = lambda n: f"20221{n:05d}"
LIST, CARD = "pw01drl00082026050620261122/28", "pw01dde0108202605061120261122/55"


def _paths(directory):
    paths = Paths(data_dir=directory)
    paths.tables_dir.mkdir(parents=True)
    races, runners = make_tables(n_days=34, races_per_day=6, field=10, horses=60)
    offset = pd.Timestamp("2026-11-15") - pd.Timestamp(races["race_date"].max())
    for df in (races, runners):
        df["race_date"] = (pd.to_datetime(df["race_date"]) + offset).dt.strftime("%Y-%m-%d")
        df["race_key"] = df["race_date"] + df["race_key"].str[10:]
        if "runner_key" in df:
            df["runner_key"] = df["race_date"] + df["runner_key"].str[10:]
            df["horse_id"] = df["horse_id"].map(lambda v: ids(int(v[1:])))
    races.to_parquet(paths.tables_dir / "races.parquet")
    runners.to_parquet(paths.tables_dir / "runners.parquet")
    return paths


def _pages(clock):
    """14:30からは3番の取消が出馬表・オッズに出ている（JRAのページが時刻で変わる）。"""
    scratched = clock["now"] >= T("14:30")
    cards = card_html([ids(i) for i in range(8)])
    if scratched:
        cards = cards.replace('<td class="num">3</td>', '<td class="num"><span class="cap">取消</span></td>', 1)
        odds = '<div class="cell time"><strong>14時30分現在オッズ</strong></div>' + "".join(
            f'<table class="basic narrow-xy fuku3"><caption>{a}-{b}</caption><tbody>'
            + "".join(f'<tr><th scope="row">{c}</th><td>{"取消" if 3 in (a, b, c) else 10.0 + a + b + c}</td></tr>'
                      for c in range(b + 1, 9)) + "</tbody></table>"
            for a, b in combinations(range(1, 9), 2) if b < 8)
        win = win_html(cell=lambda k: "取消" if k == 3 else 2.0 + k, time="14時30分")
    else:
        odds, win = trio_html(time="14時25分"), win_html(time="14時25分")
    return {LIST: race_list(odds=True).replace("15時40分", "15時41分").replace("18頭", "8頭"),
            CARD: cards, "pw15orl10/AA": ODDS, "pw151ou1/11": win, "pw156ou1/66": odds}


def test_post_time_change_moves_the_capture_window(tmp_path):
    paths = _paths(tmp_path)
    clock = {"now": T("14:25")}
    col = Collector(paths.data_dir / "live", client=FakeClient(lambda cn: _pages(clock)[cn]), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST}
    col.state["refreshed_at"] = (T("14:20")).isoformat()
    col.record_post_times()
    col._write_state()
    with patch.object(Collector, "check_notices", lambda self, log=print: None):
        first = col.tick(log=lambda *_: None)
        assert first["saved"] == 0 and f"{RK}|1h" not in col.state["taken"]   # 15:41の時間帯（14:26〜）の外では取らない
        clock["now"] = T("14:30")
        second = col.tick(log=lambda *_: None)
    assert second["saved"] == 4 and col.state["races"][RK]["post_time"] == "15:41"
    at = T("14:30") + timedelta(seconds=1)
    with patch.object(Ledger, "_now", lambda _: at.astimezone(timezone.utc).isoformat(timespec="seconds")):
        wallet.run_live(paths, now=at)
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    market = [p for p in preds if p["method"] == "ml_market"]
    assert market and all(3 in p["inputs"]["scratched_known"] for p in market)
    assert not any("3" in k.split("-") for k in market[0]["allocation"]["ev_weighted"]["stakes"])
    # 結果：3番取消・1-2-4が的中
    clock["now"] = T("16:13")
    res = result_html([1, 2, 4, 5, 6, 7, 8, 3]).replace('<td class="place">8</td><td class="num">3</td>',
                                                         '<td class="place">取消</td><td class="num">3</td>')
    col._save(RK, "result", "result", "/x", "synth", res)
    wallet.run_live(paths, now=T("16:13"))
    row = wallet.wallet_summary(paths).query("method == 'ml_market' and wallet == 'ev_weighted'").iloc[0]
    assert row["returned"] > row["stake"] > 0


def test_material_before_the_window_is_only_early():
    dl = T("14:41")      # 15:41発走の1時間前
    assert official.material_time([("card", "2026-11-22T14:25:00+09:00"), ("odds_trio", "2026-11-22T14:25:05+09:00")], dl) is None
    got = official.material_time([("card", "2026-11-22T14:30:00+09:00"), ("odds_trio", "2026-11-22T14:30:05+09:00")], dl)
    assert got == T("14:30") + timedelta(seconds=5)


def test_capture_is_reopened_when_post_moves_later(tmp_path):
    clock = {"now": T("14:26")}
    col = Collector(tmp_path, client=None, now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40", "list_cname": LIST}
    col.state["taken"][f"{RK}|1h"] = T("14:26").isoformat()
    col.state["races"][RK]["post_time"] = "16:00"                        # 発走が20分遅れた
    clock["now"] = T("14:45")
    assert (RK, "1h") in col.due()                                       # 新しい時間帯（14:45〜15:00）で取り直す
    col.state["taken"][f"{RK}|1h"] = T("14:46").isoformat()
    assert (RK, "1h") not in col.due()


def test_failed_recapture_after_post_change_does_not_bet_on_old_odds(tmp_path):
    """💚20261125-6 止める①：14:25に取ったあと発走が15:40→15:45に変わり、新しい時間帯（14:30〜14:45）の取り直しが
    失敗したまま締切を過ぎた。締切の代わりの予想は作ってよいが、時間帯より前のオッズでお金を配分しない。取り損ねとして知らせる。"""
    paths = _paths(tmp_path)
    clock = {"now": T("14:25")}
    fail = {"on": False}

    def page(cn):
        if fail["on"]:
            raise TimeoutError("JRAが応答しない")
        return _pages(clock)[cn].replace("15時41分", "15時40分")
    col = Collector(paths.data_dir / "live", client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "known_at": T("09:00").isoformat()}
    col.state["refreshed_at"] = T("14:20").isoformat()
    col.record_post_times()
    col._write_state()
    with patch.object(Collector, "check_notices", lambda self, log=print: None):
        assert col.tick(log=lambda *_: None)["saved"] == 4             # 15:40の時間帯で取った
        col.state["races"][RK]["post_time"] = "15:45"                  # 一覧の更新で発走が5分遅れた
        col._write_state()
        fail["on"] = True
        for hm in ("14:30", "14:35", "14:40"):                          # 新しい時間帯の取り直しは全部失敗
            clock["now"] = T(hm)
            col.tick(log=lambda *_: None)
        clock["now"] = T("14:46")
        col.tick(log=lambda *_: None)
    assert f"{RK}|1h" in col.missed()                                   # 新しい時間帯で取れていない＝取り損ね
    at = T("14:46") + timedelta(seconds=1)
    with patch.object(Ledger, "_now", lambda _: at.astimezone(timezone.utc).isoformat(timespec="seconds")):
        wallet.run_live(paths, now=at)
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    assert preds                                                         # 締切の代わりの予想は作る
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes"), (p["method"], a, v)       # でもお金は配分しない
    gate = next(p for p in preds if p.get("odds_gate"))["odds_gate"]
    assert any("時間帯より前" in r for r in gate["reasons"])
    # 直してから①：画面はこの予想を「早めの参考」ではなく「締切の代わりの予想」と呼ぶ（3画面とも同じ判定）
    from keiba.app.checks import card_flags, material_timing, pre_publish_check
    from keiba.app.data import Source
    src = Source(paths, "live")
    p = src.preds[RK][("ml_lgbm", "1h")]
    state, text = material_timing(p, src.races[RK], "1h")
    assert state == "fallback" and "代わり" in text and "14:45" in text, (state, text)   # 保存時の発走15:45の締切
    assert ("warn", "締切の代わりの予想") in card_flags(p, {}, src.races[RK], False, tp="1h")
    item = next(i for i in pre_publish_check(p, {}, src.races[RK], None, None, None, "1h") if i["label"] == "時点の材料")
    assert item["ok"] is False and "早めに作った参考" not in item["detail"]


def test_partial_recapture_card_only_does_not_bet_on_old_odds(tmp_path):
    """新しい時間帯で出馬表だけ取れて、オッズの取得が失敗し続けた場合も、時間帯より前のオッズではお金を配分しない。"""
    paths = _paths(tmp_path)
    clock = {"now": T("14:25")}
    fail = {"on": False}

    def page(cn):
        if fail["on"] and cn.startswith("pw15"):
            raise TimeoutError("オッズのページが応答しない")
        return _pages(clock)[cn].replace("15時41分", "15時45分" if fail["on"] else "15時40分")   # 一覧も15:45を出す
    col = Collector(paths.data_dir / "live", client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "known_at": T("09:00").isoformat()}
    col.state["refreshed_at"] = T("14:20").isoformat()
    col.record_post_times()
    col._write_state()
    with patch.object(Collector, "check_notices", lambda self, log=print: None):
        col.tick(log=lambda *_: None)
        col.state["races"][RK]["post_time"] = "15:45"
        col._write_state()
        fail["on"] = True
        for hm in ("14:30", "14:35", "14:40", "14:46"):
            clock["now"] = T(hm)
            col.tick(log=lambda *_: None)
    assert f"{RK}|1h" in col.missed()
    at = T("14:46") + timedelta(seconds=1)
    with patch.object(Ledger, "_now", lambda _: at.astimezone(timezone.utc).isoformat(timespec="seconds")):
        wallet.run_live(paths, now=at)
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    assert preds
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes"), (p["method"], a, v)
    # 💚20261125-10 直してから①：出馬表だけ時間帯の中で取り直せても、材料はそろっていない。正式の判定どおり、
    # 3画面とも「締切の代わりの予想」と呼び、公開前チェックの「時点の材料」は合格にしない
    from keiba.app.checks import card_flags, material_timing, pre_publish_check
    from keiba.app.data import Source
    src = Source(paths, "live")
    p = src.preds[RK][("ml_lgbm", "1h")]
    assert p["_fallback"]
    state, text = material_timing(p, src.races[RK], "1h")
    assert state == "fallback" and "代わり" in text, (state, text)
    assert ("warn", "締切の代わりの予想") in card_flags(p, {}, src.races[RK], False, tp="1h")
    item = next(i for i in pre_publish_check(p, {}, src.races[RK], None, None, None, "1h") if i["label"] == "時点の材料")
    assert item["ok"] is False
