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


def trio_html(time="14時25分", dup=False, drop=False):
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


def _setup(tmp_path, trio=None, card=None, at=(14, 30), tp="1h"):
    paths = Paths(data_dir=tmp_path)
    races, runners = make_tables(n_days=34, races_per_day=6, field=10, horses=60)
    paths.tables_dir.mkdir(parents=True)
    races.to_parquet(paths.tables_dir / "races.parquet")
    runners.to_parquet(paths.tables_dir / "runners.parquet")
    ids = [f"H{h:04d}" for h in range(N)]
    root = tmp_path / "live"
    col = Collector(root, client=None, now=lambda: datetime(2025, 8, 30, *at, tzinfo=JST))
    col.state["races"][RK] = {"race_date": "2025-08-30", "venue": "東京", "race_number": 11, "post_time": "15:40",
                              "surface": "芝", "distance_m": 1600, "field_size": N}
    for kind, page in (("card", card or card_html(ids)), ("odds_win", win_html()), ("odds_trio", trio or trio_html())):
        col._save(RK, kind, tp, "/JRADB/x", "c", page)
    col._write_state()
    return paths, col


def test_live_predict_allocate_settle(tmp_path):
    # 合成ページの馬IDは "H0000" 形式（make_tables と同じ）。カードの読取は10桁を期待するので、読み替える
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        out = run_live(paths)
        assert out["predicted"] == 2 + 6 + 3  # 財布2方式 ＋ ルール6版（比較用・配分なし）＋ 統合3版
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
        assert out["settled"] == 3 * 5  # （財布2方式＋統合3版）× 3配分
        w = wallet_summary(paths)
        assert set(w["wallet"]) == {"equal_payout", "ev_weighted", "kelly"} and (w["stake"] <= 10_000).all()
    finally:
        jra.parse_card = orig


def test_partial_result_waits_for_full_result(tmp_path):
    """結果を一部しか読めない（8頭中2頭）なら精算を書かない。完全な結果が来たら、そこで精算する。"""
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        run_live(paths)
        col._save(RK, "result", "result", "/JRADB/x", "c", result_html([3, 1]))
        assert run_live(paths)["settled"] == 0              # 不成立の返還にはしない
        col._save(RK, "result", "result", "/JRADB/x", "c2", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
        assert run_live(paths)["settled"] == 3 * 5
        from keiba.ledger import Ledger
        led = Ledger(paths.ledger_path, read_only=True)
        assert not [s for s in led.settlements() if s["status"] == "refunded_void"]
        led.close()
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
    assert r["pass"] and r["trio"] == "56/56" and r["odds_time"] == "14:25" and len(r["snapshot_sha256"]) == 64
    assert r["deadline"] == "2025-08-30T14:40+09:00"


def test_gate_rejects_duplicates(tmp_path):
    r = _gate(tmp_path, trio=trio_html(dup=True))
    assert not r["pass"] and "重複" in r["reasons"]


def test_gate_rejects_missing_combos(tmp_path):
    r = _gate(tmp_path, trio=trio_html(drop=True))
    assert not r["pass"] and "組の数" in r["reasons"]


def test_gate_rejects_stale_and_unknown_time(tmp_path):
    assert "古いオッズ" in _gate(tmp_path, trio=trio_html(time="14時00分"))["reasons"]


def test_gate_rejects_capture_after_time_point_deadline(tmp_path):
    # 発走10分前に取ったものを「15分前」の合格にしない
    from keiba.live.wallet import odds_check
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, _ = _setup(tmp_path, trio=trio_html(time="15時29分"), at=(15, 30), tp="15m")
        df = odds_check(paths)
    finally:
        jra.parse_card = orig
    # 出馬表も締切（15:25）後なので、15m の入力そのものが作られない
    assert df.empty or not df.set_index("time_point").loc["15m", "pass"]
    assert "時刻が分からない" in _gate(tmp_path / "b", trio=trio_html(time=None))["reasons"]


def test_known_scratch_reduces_field(tmp_path):
    ids = [f"H{h:04d}" for h in range(N)]
    card = card_html(ids).replace('<td class="num">8</td>', '<td class="num">8</td><td class="cancel">取消</td>')
    r = _gate(tmp_path, card=card)
    assert r["scratched_known"] == "8" and r["trio"] == "56/35" and not r["pass"]  # 8頭の組は7頭の35組と合わない


def test_monthly_review(tmp_path):
    from keiba.review import build_review, write_review
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        run_live(paths)
        col._save(RK, "result", "result", "/JRADB/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
        run_live(paths)
        r = build_review(paths, "2025-08")
        assert r["races_predicted"] == 1 and r["races_with_result"] == 1
        assert {w["wallet"] for w in r["wallets"]} == {"equal_payout", "ev_weighted", "kelly"}
        assert {a["method"] for a in r["accuracy"]} >= {"ml_lgbm", "rule"}
        assert r["odds_gate"][0]["passed"] == 1 and "判定はしない" in r["judgement"]
        assert sum(x["races"] for x in r["role_a"]) == 1
        assert write_review(paths, "2025-08").exists()
    finally:
        jra.parse_card = orig


def test_mam_export_and_import(tmp_path):
    from keiba.live.mam import export_packets, import_predictions
    from keiba.ledger import Ledger
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        files = export_packets(paths)
        assert len(files) == 1
        packet = json.loads(files[0].read_text())
        assert packet["deadline"] == "2025-08-30T14:40+09:00" and len(packet["horses"]) == N
        assert not any(k in packet for k in ("trios", "rule_view", "predictions"))
        base = {"schema": "keiba-mam-prediction-v1", "race_key": RK, "time_point": "1h", "author": "mam"}
        good = dict(base, generated_at="2025-08-30T14:35:00+09:00", ranking=[3, 1, 2],
                    trios=[{"combo": "1-2-3", "p": 0.2}, {"combo": "1-2-4", "p": 0.1}], comment="テスト")
        late = dict(base, generated_at="2025-08-30T14:45:00+09:00", ranking=[3, 1, 2])
        bad = dict(base, generated_at="2025-08-30T14:35:00+09:00", ranking=[3, 99])
        f = tmp_path / "mam.json"
        f.write_text(json.dumps([good, late, bad]))
        out = import_predictions(paths, f)
        assert out["accepted"] == 1 and len(out["rejected"]) == 2
        assert "締切後" in out["rejected"][0]["reason"]
        led = Ledger(paths.ledger_path, read_only=True)
        p = next(x for x in led.predictions() if x["method"] == "text_ai")
        led.close()
        assert p["trios"][0]["combo"] == "1-2-3" and abs(p["trios"][0]["p"] - 2 / 3) < 1e-6
        col._save(RK, "result", "result", "/JRADB/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
        run_live(paths)
        w = wallet_summary(paths)
        assert "text_ai" in set(w["method"])
    finally:
        jra.parse_card = orig


def test_integrated_versions(tmp_path):
    from keiba.ledger import Ledger
    from keiba.review import build_review
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        run_live(paths)
        led = Ledger(paths.ledger_path, read_only=True)
        p = {x["method"]: x for x in led.predictions()}
        led.close()
        mk, mm, mam = p["int_market"], p["int_market_ml"], p["int_market_ml_mam"]
        assert mk["formal_probability"] and abs(sum(mk["trio_probs_full"].values()) - 1) < 1e-4
        assert mm["weights"] == {"market": 1.0, "ml": 0.2} and mm["allocation"]["kelly"]["status"] != "not_allocated"
        # まむの予想が締切までに無い → 黙って外さず、理由つきで未配分
        assert not mam["formal_probability"] and "まむ：締切までに予想が無い" in mam["reasons"]
        assert mam["allocation"]["ev_weighted"]["status"] == "not_allocated"
        col._save(RK, "result", "result", "/JRADB/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
        run_live(paths)
        cmp = build_review(paths, "2025-08")["comparison"]
        assert {c["method"] for c in cmp} == {"市場推定確率のみ（基準）", "学習のみ", "市場＋学習 1.0/0.2"} and all(c["races"] == 1 for c in cmp)
    finally:
        jra.parse_card = orig


def test_mam_partial_trios_are_reference_only(tmp_path):
    from keiba.ledger import Ledger
    from keiba.live import wallet
    from keiba.live.mam import import_predictions
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        f = tmp_path / "mam.json"
        f.write_text(json.dumps({"schema": "keiba-mam-prediction-v1", "race_key": RK, "time_point": "1h",
                                 "generated_at": "2025-08-30T14:35:00+09:00", "trios": [{"combo": "1-2-3", "p": 0.3}]}))
        assert import_predictions(paths, f)["accepted"] == 1
        run_live(paths)
        led = Ledger(paths.ledger_path, read_only=True)
        p = next(x for x in led.predictions() if x["method"] == "int_market_ml_mam")
        led.close()
        assert not p["formal_probability"] and p["trio_probs_full"] is None and p["trios"]
        assert any("全組の確率がそろっていない" in r for r in p["reasons"])
    finally:
        jra.parse_card = orig
