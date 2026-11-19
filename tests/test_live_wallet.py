"""これからのレースの予想・配分・精算（合成のページで端から端まで。ネットにはつながない）。"""
import json
from datetime import datetime
from itertools import combinations

import pandas as pd

from keiba.config import Paths
from keiba.live import jra
from keiba.live.collector import JST, Collector
from keiba.live.wallet import run_live, wallet_summary
from tests.conftest import make_tables

RK = "2025-08-30|東京|11"
N = 8


def card_html(ids):
    rows = "".join(f"""<tr><td class="waku"><img alt="枠{(i + 1) // 2 + 1}"></td><td class="num">{i + 1}</td><td class="horse">
<div class="name"><a href="/JRADB/accessU.html?CNAME=pw01dud00{h}/56">ウマ{i}</a></div>
<p class="trainer"><a href="#" onclick="return doAction('/JRADB/accessC.html', 'pw05cmk00100{i % 5}/7F');">調教</a></p>
<ul><li class="sire">父：チチ</li></ul></td>
<td class="jockey"><p class="age">牡3/鹿</p><p class="weight">56.0<span>kg</span></p>
<p class="jockey"><a href="#" onclick="return doAction('/JRADB/accessK.html', 'pw04kmk00200{i % 7}/8B');">騎手</a></p></td></tr>"""
                   for i, h in enumerate(ids))
    return f"<table><tbody>{rows}</tbody></table>"


def win_html():
    return "<table>" + "".join(f'<tr><td class="num">{n}</td><td class="odds_tan">{2.0 + n}</td></tr>' for n in range(1, N + 1)) + "</table>"


def trio_html(time="14時45分", dup=False, drop=False):
    out = [f"<p>{time}現在のオッズ</p>"] if time else []
    for a, b in combinations(range(1, N + 1), 2):
        rows = "".join(f'<tr><th class="num">{c}</th><td class="odds">{10.0 + a + b + c}</td></tr>' for c in range(b + 1, N + 1))
        if rows:
            out.append(f"<table><caption>{a}-{b}</caption>{rows}</table>")
    if dup:
        out.append('<table><caption>1-2</caption><tr><th class="num">3</th><td class="odds">99.9</td></tr></table>')
    if drop:
        out = out[:-1]
    return "".join(out)


def result_html(order):
    rows = "".join(f'<tr><td class="place">{p}</td><td class="num">{n}</td><td class="horse"><a href="#">x</a></td>'
                   f'<td class="time">1:35.{p}</td><td class="f_time">34.{p}</td><td class="pop">{p}</td></tr>' for p, n in enumerate(order, 1))
    combo = "-".join(map(str, sorted(order[:3])))
    return (f'<table class="basic narrow-xy striped"><tbody>{rows}</tbody></table>'
            f'<li class="trio"><dl><dd><div class="line"><div class="num">{combo}</div><div class="yen">4,560円</div></div></dd></dl></li>')


def test_parsers_self_check():
    assert jra.parse_win_odds(win_html(), N)["ok"]
    assert not jra.parse_win_odds(win_html(), N + 1)["ok"]
    t = jra.parse_trio_odds(trio_html(), N)
    assert t["ok"] and len(t["odds"]) == 56 and t["odds"]["1-2-3"] == 16.0
    assert not jra.parse_trio_odds(trio_html(), N + 1)["ok"]  # 組の数が合わなければ使わない
    r = jra.parse_result(result_html([3, 1, 2, 4, 5, 6, 7, 8]))
    assert r["trio_payouts"] == [{"combo": "1-2-3", "payout_yen": 4560}] and r["rows"][0]["finish_time_s"] == 95.1


def _setup(tmp_path, trio=None, card=None):
    paths = Paths(data_dir=tmp_path)
    races, runners = make_tables(n_days=34, races_per_day=6, field=10, horses=60)
    paths.tables_dir.mkdir(parents=True)
    races.to_parquet(paths.tables_dir / "races.parquet")
    runners.to_parquet(paths.tables_dir / "runners.parquet")
    ids = [f"H{h:04d}" for h in range(N)]
    root = tmp_path / "live"
    col = Collector(root, client=None, now=lambda: datetime(2025, 8, 30, 14, 50, tzinfo=JST))
    col.state["races"][RK] = {"race_date": "2025-08-30", "venue": "東京", "race_number": 11, "post_time": "15:40",
                              "surface": "芝", "distance_m": 1600, "field_size": N}
    for kind, page in (("card", card or card_html(ids)), ("odds_win", win_html()), ("odds_trio", trio or trio_html())):
        col._save(RK, kind, "1h", "/JRADB/x", "c", page)
    col._write_state()
    return paths, col


def test_live_predict_allocate_settle(tmp_path):
    # 合成ページの馬IDは "H0000" 形式（make_tables と同じ）。カードの読取は10桁を期待するので、読み替える
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        out = run_live(paths)
        assert out["predicted"] == 2 + 6  # 財布2方式 ＋ ルール6版（比較用・配分なし）
        assert run_live(paths)["predicted"] == 0  # 二度目は何もしない
        from keiba.ledger import Ledger
        led = Ledger(paths.ledger_path, read_only=True)
        preds = {p["method"]: p for p in led.predictions()}
        led.close()
        assert preds["ml_lgbm"]["odds_gate"]["pass"]
        assert preds["rule"]["allocation"]["status"] == "not_allocated_role_a"
        view = preds["ml_lgbm"]["rule_view"]
        assert len(view["top"]) == 3 and view["top"][0]["evaluated"].endswith("/8") and view["used_for_allocation"] is False
        col._save(RK, "result", "result", "/JRADB/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
        out = run_live(paths)
        assert out["settled"] == 6  # 財布2方式 × 3配分
        w = wallet_summary(paths)
        assert set(w["wallet"]) == {"equal_payout", "ev_weighted", "kelly"} and (w["stake"] <= 10_000).all()
    finally:
        jra.parse_card = orig


def test_missed_capture_is_reported(tmp_path):
    col = Collector(tmp_path, client=None, now=lambda: datetime(2025, 8, 30, 16, 0, tzinfo=JST))
    col.state["races"][RK] = {"race_date": "2025-08-30", "venue": "東京", "race_number": 11, "post_time": "15:40",
                              "known_at": "2025-08-28T12:00:00+09:00"}
    assert set(col.missed()) == {f"{RK}|24h", f"{RK}|1h", f"{RK}|15m"}


def _gate(tmp_path, **kw):
    from keiba.live.wallet import odds_check
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, _ = _setup(tmp_path, **kw)
        return odds_check(paths).iloc[0]
    finally:
        jra.parse_card = orig


def test_gate_passes_clean_odds(tmp_path):
    r = _gate(tmp_path)
    assert r["pass"] and r["trio"] == "56/56" and r["odds_time"] == "14:45" and len(r["snapshot_sha256"]) == 64


def test_gate_rejects_duplicates(tmp_path):
    r = _gate(tmp_path, trio=trio_html(dup=True))
    assert not r["pass"] and "重複" in r["reasons"]


def test_gate_rejects_missing_combos(tmp_path):
    r = _gate(tmp_path, trio=trio_html(drop=True))
    assert not r["pass"] and "組の数" in r["reasons"]


def test_gate_rejects_stale_and_unknown_time(tmp_path):
    assert "古いオッズ" in _gate(tmp_path, trio=trio_html(time="14時00分"))["reasons"]
    assert "時刻が分からない" in _gate(tmp_path / "b", trio=trio_html(time=None))["reasons"]


def test_known_scratch_reduces_field(tmp_path):
    ids = [f"H{h:04d}" for h in range(N)]
    card = card_html(ids).replace('<td class="num">8</td>', '<td class="num">8</td><td class="cancel">取消</td>')
    r = _gate(tmp_path, card=card)
    assert r["scratched_known"] == "8" and r["trio"] == "56/35" and not r["pass"]  # 8頭の組は7頭の35組と合わない
