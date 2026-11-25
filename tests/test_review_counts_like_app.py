"""💚20261124-2 直してから②③：月次の振り返り（CLI）も、アプリと同じく
参考表示の統合予想を的中率に入れず、公表途中の結果では的中を数えない。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

from keiba.config import Paths
from keiba.ledger import Ledger
from keiba.live import wallet
from keiba.live.collector import Collector, result_issue
from keiba.review import build_review
from tests.test_live_wallet import card_html, result_html, trio_html, win_html

DAY = "2026-11-22"
RK = f"{DAY}|東京|3"
POST = datetime.fromisoformat(f"{DAY}T15:40:00+09:00")
M = "ml_lgbm"


def _ts(t):
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _scenario(tmp):
    paths = Paths(data_dir=tmp)
    col = Collector(tmp / "live", client=SimpleNamespace(count=0), now=lambda: POST - timedelta(days=2))
    col.state["races"][RK] = {"race_date": DAY, "venue": "東京", "race_number": 3, "post_time": "15:40",
                              "race_name": "3歳以上1勝クラス", "field_size": 8, "surface": "芝", "distance_m": 1600}
    col.record_post_times()
    col._write_state()
    return paths, col


def _save(col, at, kind, tp, page):
    col.now = lambda: at
    col._save(RK, kind, tp, "/x", "synth", page)


def test_reference_integrated_prediction_is_not_in_monthly_accuracy(tmp_path):
    paths, col = _scenario(tmp_path)
    mat, at = POST - timedelta(minutes=68), POST - timedelta(minutes=59)
    _save(col, mat, "card", "1h", card_html(range(8)))
    _save(col, mat, "odds_trio", "1h", trio_html(time="14時30分"))
    _save(col, mat, "odds_win", "1h", win_html(time="14時30分"))
    _, _, inputs = wallet.live_tables(col.root, col.state, wallet._snapshots(col.root))
    led = Ledger(paths.ledger_path)
    led._now = lambda: _ts(at)
    g = pd.DataFrame({"horse_number": list(range(1, 9)), "race_date": pd.to_datetime([DAY] * 8)})
    ml = SimpleNamespace(score=lambda frame: [10, 9, 8, 7, 6, 5, 4, 3])
    cal = SimpleNamespace(strength=lambda scores: __import__("numpy").exp(scores), gamma=1.0)
    wallet._integrated(led, RK, "1h", inputs[(RK, "1h")], g, {M: (ml, cal)}, "live-test", set(), {}, col.state, at)
    ref = next(p for p in led.predictions() if p["method"] == "int_market_ml_mam")
    assert ref["formal_probability"] is False                      # まむがまだ来ない：参考表示
    _save(col, POST + timedelta(minutes=31), "result", "result", result_html(list(range(1, 9))))
    _, _, inputs = wallet.live_tables(col.root, col.state, wallet._snapshots(col.root))
    wallet._settle(led, inputs, col.state, col.root, POST + timedelta(hours=1))
    led.commit()
    led.close()
    acc = {r["method"] for r in build_review(paths, DAY[:7])["accuracy"]}
    assert "int_market_ml_mam" not in acc and "int_market_ml" in acc


def test_partial_result_is_not_counted_in_monthly(tmp_path):
    paths, col = _scenario(tmp_path)
    mat = POST - timedelta(minutes=66)
    _save(col, mat, "card", "1h", card_html(range(8)))
    _save(col, mat, "odds_trio", "1h", trio_html(time="14時30分"))
    led = Ledger(paths.ledger_path)
    led._now = lambda: _ts(POST - timedelta(minutes=65))
    led.add_prediction(RK, "1h", M, "live-test", {"race_date": DAY, "period": "live", "horses": [],
                                                  "trios": [{"combo": "1-2-3", "p": .2}],
                                                  "allocation": {a: {"status": "allocated", "stakes": {"1-2-3": 200}, "total": 200}
                                                                 for a in wallet.ALLOC_METHODS}}, "i", "m", "c")
    led.commit()
    partial = result_html([1, 2, 3])                                # 8頭中3行だけ公表
    assert result_issue(partial, 8)
    _save(col, POST + timedelta(minutes=31), "result", "result", partial)
    led.close()
    r = build_review(paths, DAY[:7])
    assert r["races_with_result"] == 0 and not r["accuracy"]
    _save(col, POST + timedelta(minutes=36), "result", "result", result_html(list(range(1, 9))))   # そろった
    r = build_review(paths, DAY[:7])
    hit = next(x for x in r["accuracy"] if x["method"] == M)
    assert r["races_with_result"] == 1 and hit["上位5"] == 1.0


def _tie_result():
    """3着同着（3番と4番）：的中は 1-2-3 と 1-2-4 の2組。"""
    places = [1, 2, 3, 3, 5, 6, 7, 8]
    rows = "".join(f'<tr><td class="place">{p}</td><td class="num">{n}</td><td class="horse"><a href="#">x</a></td>'
                   f'<td class="time">1:35.{i}</td><td class="f_time">34.{i}</td><td class="pop">{i}</td></tr>'
                   for i, (p, n) in enumerate(zip(places, range(1, 9)), 1))
    pays = "".join(f'<div class="line"><div class="num">{c}</div><div class="yen">{y}円</div></div>'
                   for c, y in (("1-2-3", "7,890"), ("1-2-4", "4,560")))
    return (f'<html><body><table class="basic narrow-xy striped"><tbody>{rows}</tbody></table>'
            f'<li class="trio"><dl><dd>{pays}</dd></dl></li></body></html>')


def test_dead_heat_for_third_counts_both_winning_combos(tmp_path):
    """💚20261125-6 直してから②：3着同着の2組のどちらを上位に置いていても、月次の的中に数える。"""
    paths, col = _scenario(tmp_path)
    mat = POST - timedelta(minutes=66)
    _save(col, mat, "card", "1h", card_html(range(8)))
    _save(col, mat, "odds_trio", "1h", trio_html(time="14時30分"))
    led = Ledger(paths.ledger_path)
    led._now = lambda: _ts(POST - timedelta(minutes=65))
    led.add_prediction(RK, "1h", M, "live-test", {"race_date": DAY, "period": "live", "horses": [],
                                                  "trios": [{"combo": "1-2-4", "p": .2}] + [{"combo": f"5-6-{k}", "p": .01} for k in (7, 8)],
                                                  "allocation": {a: {"status": "allocated", "stakes": {"1-2-4": 100}, "total": 100}
                                                                 for a in wallet.ALLOC_METHODS}}, "i", "m", "c")
    led.commit()
    led.close()
    _save(col, POST + timedelta(minutes=31), "result", "result", _tie_result())
    r = build_review(paths, DAY[:7])
    hit = next(x for x in r["accuracy"] if x["method"] == M)
    assert r["races_with_result"] == 1 and hit["上位5"] == 1.0, hit
