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


def win_html(n=N, cell=None, time="14時25分"):
    """実物と同じ形の単勝・複勝の表（class に tanpuku、td.num・td.odds_tan）と発表時刻の欄。cell で1頭分のセルを差し替えられる。"""
    rows = "".join(f'<tr><td class="num">{k}</td><td class="horse"><a>x</a></td>'
                   f'<td class="odds_tan">{cell(k) if cell else 2.0 + k}</td><td class="odds_fuku">1.1-1.5</td></tr>'
                   for k in range(1, n + 1))
    head = f'<div class="cell time"><strong>{time}現在オッズ</strong></div>' if time else ""
    return head + f'<table class="basic narrow-xy tanpuku"><thead><tr><th class="num">馬番</th><th class="odds_tan">単勝</th></tr></thead><tbody>{rows}</tbody></table>'


def trio_html(time="14時25分", dup=False, drop=False, n=N):
    """実物と同じ形の三連複の表（class に fuku3、caption「a-b」、行は th・td）。"""
    out = [f'<div class="cell time"><strong>{time}現在オッズ</strong></div>'] if time else []
    for a, b in combinations(range(1, n + 1), 2):
        rows = "".join(f'<tr><th scope="row">{c}</th><td>{10.0 + a + b + c}</td></tr>' for c in range(b + 1, n + 1))
        if rows:
            out.append(f'<table class="basic narrow-xy fuku3"><caption>{a}-{b}</caption><tbody>{rows}</tbody></table>')
    if dup:
        out.append('<table class="basic narrow-xy fuku3"><caption>1-2</caption><tbody><tr><th scope="row">3</th><td>99.9</td></tr></tbody></table>')
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


def _setup(tmp_path, trio=None, card=None, at=(14, 30), tp="1h", day=30, win=None):
    paths = Paths(data_dir=tmp_path)
    races, runners = make_tables(n_days=34, races_per_day=6, field=10, horses=60)
    paths.tables_dir.mkdir(parents=True)
    races.to_parquet(paths.tables_dir / "races.parquet")
    runners.to_parquet(paths.tables_dir / "runners.parquet")
    ids = [f"H{h:04d}" for h in range(N)]
    root = tmp_path / "live"
    col = Collector(root, client=None, now=lambda: datetime(2025, 8, day, *at, tzinfo=JST))
    col.state["races"][RK] = {"race_date": "2025-08-30", "venue": "東京", "race_number": 11, "post_time": "15:40",
                              "surface": "芝", "distance_m": 1600, "field_size": N}
    for kind, page in (("card", card or card_html(ids)), ("odds_win", win or win_html()), ("odds_trio", trio or trio_html())):
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
    assert not r["pass"] and "全組と合わない" in r["reasons"]


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
    card = card_html(ids).replace('<td class="num">8</td>', '<td class="num"><span class="cap">取消</span></td>')  # 実物の取消の形
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
    from keiba.live.mam import export_packets, import_predictions
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    try:
        paths, col = _setup(tmp_path)
        export_packets(paths)  # ふだんの流れ：まむへの入力を渡してから回答を受け付ける
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


def test_trio_odds_real_page_shape():
    """実物（2026-11-21 確認）は class の無い <th scope="row">馬番</th><td>オッズ</td>。この形で全組が読めること。"""
    from math import comb
    n = 6
    tables = []
    for a, b in combinations(range(1, n + 1), 2):
        rows = "".join(f'<tr>\n<th scope="row">{c}</th><td>{100.0 + a * 10 + b + c / 10}</td></tr>' for c in range(b + 1, n + 1))
        if rows:
            tables.append(f'<li><table class="basic narrow-xy fuku3">\n<caption>{a}-{b}</caption><tbody>{rows}</tbody></table></li>')
    page = '<div class="cell time"><strong>10時49分現在オッズ</strong></div><ul>' + "".join(tables) + "</ul>"
    t = jra.parse_trio_odds(page, n)
    assert t["ok"] and len(t["odds"]) == comb(n, 3) and t["odds"]["1-2-3"] == 112.3
    assert jra.parse_odds_time(page) == "10:49"
    assert not jra.parse_trio_odds(page, n + 1)["ok"]


def _fuku3(pairs_rows, extra=""):
    """実物の形の三連複ページ。pairs_rows：{(a, b): [(c, "倍率"), ...]}"""
    tabs = "".join(f'<li><table class="basic narrow-xy fuku3"><caption>{a}-{b}</caption><tbody>'
                   + "".join(f'<tr><th scope="row">{c}</th><td>{v}</td></tr>' for c, v in rows) + "</tbody></table></li>"
                   for (a, b), rows in pairs_rows.items())
    return f'<div class="cell time"><strong>10時49分現在オッズ</strong></div><ul>{tabs}</ul>{extra}'


def _all_rows(nums, skip=()):
    out = {}
    for a, b in combinations(nums, 2):
        rows = [(c, f"{10 + a + b + c}.0") for c in nums if c > b and (a, b, c) not in skip]
        if rows:
            out[(a, b)] = rows
    return out


def test_trio_odds_must_match_active_horses_exactly():
    """みに指摘（PR #17 重大）：組の数が同じでも、取消の馬を含む組や出走馬の組が欠けていれば不合格。"""
    page = _fuku3(_all_rows([1, 2, 3, 4]))           # 1〜4番の全4組
    t = jra.parse_trio_odds(page, [1, 2, 3, 5])        # 出走馬は 1・2・3・5（4番は取消）
    assert not t["ok"] and t["found"] == t["expected"] == 4 and t["extra"] == 3 and t["missing"] == 3
    assert jra.parse_trio_odds(page, [1, 2, 3, 4])["ok"]
    assert not jra.parse_win_odds('<table><tr><td class="num">1</td><td class="odds_tan">2.0</td></tr>'
                                  '<tr><td class="num">4</td><td class="odds_tan">3.0</td></tr></table>', [1, 2])["ok"]


def test_trio_odds_reads_only_fuku3_tables():
    """みに指摘（PR #17 要修正）：三連複の表の行が欠けたとき、別の表の同じ馬番・数値で埋めて合格にしない。"""
    rows = _all_rows([1, 2, 3, 4], skip={(2, 3, 4)})
    note = '<table class="note"><caption>2-3</caption><tr><th scope="row">4</th><td>12.3</td></tr></table>'
    t = jra.parse_trio_odds(_fuku3(rows, extra=note), [1, 2, 3, 4])
    assert not t["ok"] and t["missing"] == 1 and "2-3-4" not in t["odds"]


def test_trio_odds_scratched_row_on_real_page():
    """取消の馬の行が「取消」と表示されても、残りの馬の全組がそろえば合格。"""
    rows = _all_rows([1, 2, 3, 4, 5])
    for k, r in rows.items():
        rows[k] = [(c, "取消" if 4 in (k[0], k[1], c) else v) for c, v in r]
    t = jra.parse_trio_odds(_fuku3(rows), [1, 2, 3, 5])
    assert t["ok"] and len(t["odds"]) == 4 and t["unreadable"] == 6


def test_roster_with_gaps_or_duplicates_is_rejected():
    """みに指摘（再検証 重大）：名簿が欠けている・重複しているときは、組が一致しても合格にしない。"""
    from keiba.live.wallet import card_problem
    page = _fuku3({(1, 2): [(5, "12.0")]})
    assert not jra.parse_trio_odds(page, [1, 2, 5, 5])["ok"]  # 名簿の重複
    assert not jra.parse_win_odds('<table><tr><td class="num">1</td><td class="odds_tan">2.0</td></tr></table>', [1, 1])["ok"]
    assert card_problem([1, 2, 4, 5, 5], 5) == "出馬表の馬番が重複している"
    assert card_problem([1, 2, 4, 5], 5) == "出馬表の馬番が欠けている"
    assert "頭数" in card_problem([1, 2, 3, 4], 5)
    assert card_problem([1, 2, 3, 4, 5], 5) is None


def test_gate_fails_on_broken_card(tmp_path):
    """出馬表から3番が抜けたページでは、オッズがそろっていても合格にしない（配分しない）。"""
    ids = [f"H{h:04d}" for h in range(N)]
    html = card_html(ids)
    # 3番の行を消す（名簿の欠け）
    rows = html.split("<tr>")
    broken = "<tr>".join(r for r in rows if '<td class="num">3</td>' not in r)
    r = _gate(tmp_path, card=broken)
    assert not r["pass"] and "欠けている" in r["reasons"]


def test_trio_odds_ignores_nested_tables_and_extra_cells():
    """みに指摘（再検証 要修正）：fuku3 の中の入れ子の表や、倍率の前の余分な数字のセルを倍率に使わない。"""
    rows = _all_rows([1, 2, 3, 4], skip={(2, 3, 4)})
    page = _fuku3(rows).replace(
        "<caption>2-3</caption><tbody>",
        '<caption>2-3</caption><tbody><tr><td><table class="note"><tr><th scope="row">4</th><td>12.3</td></tr></table></td></tr>')
    t = jra.parse_trio_odds(page, [1, 2, 3, 4])
    assert not t["ok"] and "2-3-4" not in t["odds"]
    rows = _all_rows([1, 2, 3, 4])
    page = _fuku3(rows).replace('<th scope="row">3</th><td>16.0</td>', '<th scope="row">3</th><td>1</td><td>123.5</td>', 1)
    t = jra.parse_trio_odds(page, [1, 2, 3, 4])
    assert not t["ok"] and t["odds"].get("1-2-3") is None


def _live_inputs(tmp_path, **kw):
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path, **kw)
    root = tmp_path / "live"
    state = json.loads((root / "state.json").read_text())
    return paths, col, root, state, live_tables(root, state, _snapshots(root))[2][(RK, "1h")]


def test_broken_or_unknown_roster_blocks_win_odds_too(tmp_path):
    """みに指摘（3回目 重大）：名簿の末尾が欠けたとき・頭数が分からないときは、単勝も三連複も使わない。"""
    from keiba.live.wallet import _snapshots, card_problem, live_tables
    assert "分からない" in card_problem([1, 2, 3], None)
    ids = [f"H{h:04d}" for h in range(N)]
    rows = card_html(ids).split("<tr>")
    tail_missing = "<tr>".join(r for r in rows if f'<td class="num">{N}</td>' not in r)
    *_, inp = _live_inputs(tmp_path / "a", card=tail_missing)
    assert not inp["gate"]["pass"] and not inp["win"]["ok"] and "頭数" in inp["win"]["reason"]
    paths, col, root, state, inp = _live_inputs(tmp_path / "b")
    assert inp["gate"]["pass"] and inp["win"]["ok"]
    state["races"][RK]["field_size"] = None
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False))
    inp = live_tables(root, state, _snapshots(root))[2][(RK, "1h")]
    assert not inp["gate"]["pass"] and not inp["win"]["ok"]


def test_trio_odds_rows_need_exactly_one_th_and_td():
    """みに指摘（3回目 要修正）：th が2つの行の最初の td を倍率にしない。倍率は有限の正の数だけ。"""
    rows = _all_rows([1, 2, 3, 4])
    page = _fuku3(rows).replace('<th scope="row">3</th><td>16.0</td>',
                                '<th scope="row">3</th><th>x</th><td>1</td><td>123.5</td>', 1)
    t = jra.parse_trio_odds(page, [1, 2, 3, 4])
    assert not t["ok"] and "1-2-3" not in t["odds"]
    page = _fuku3(rows).replace('<td>16.0</td>', "<td>" + "9" * 400 + "</td>", 1)
    t = jra.parse_trio_odds(page, [1, 2, 3, 4])
    assert not t["ok"] and "1-2-3" not in t["odds"]


# ---- 読み取りの検査（みにの4回の独立検証の指摘を、種類ごとにまとめて確かめる） ----
import pytest

BAD_NUMBERS = ["9" * 400, "2,3,4.5", "1,23.4", "0", "0.0", "-1.0", "1e5", "inf", "NaN", "12.3倍", "", "取消", "1000000.0"]


@pytest.mark.parametrize("text", BAD_NUMBERS)
def test_win_odds_rejects_bad_numbers(text):
    w = jra.parse_win_odds(win_html(cell=lambda k: text if k == 3 else 2.0 + k), list(range(1, N + 1)))
    assert not w["ok"] and 3 not in w["odds"]


@pytest.mark.parametrize("text", BAD_NUMBERS)
def test_trio_odds_rejects_bad_numbers(text):
    page = trio_html().replace('<th scope="row">3</th><td>16.0</td>', f'<th scope="row">3</th><td>{text}</td>', 1)
    t = jra.parse_trio_odds(page, list(range(1, N + 1)))
    assert not t["ok"] and "1-2-3" not in t["odds"]


def test_good_number_forms_are_read():
    assert jra._num("1,234.5") == 1234.5 and jra._num("12.3") == 12.3 and jra._num("7") == 7.0
    w = jra.parse_win_odds(win_html(cell=lambda k: "1,234.5" if k == 1 else 2.0 + k), list(range(1, N + 1)))
    assert w["ok"] and w["odds"][1] == 1234.5


def _win_rows(n=N):
    return "".join(f'<tr><td class="num">{k}</td><td class="odds_tan">{2.0 + k}</td></tr>' for k in range(1, n + 1))


@pytest.mark.parametrize("page", [
    # 単勝の表の外（注記の表）に1頭分を置く
    win_html(n=N - 1) + f'<table class="note"><tr><td class="num">{N}</td><td class="odds_tan">9.9</td></tr></table>',
    # 単勝の表が2つ
    win_html() + win_html(),
    # 入れ子の表の中の行
    win_html(n=N - 1).replace("</tbody>", f'<tr><td><table><tr><td class="num">{N}</td><td class="odds_tan">9.9</td></tr></table></td></tr></tbody>'),
    # 馬番の欄が2つある行
    win_html().replace('<td class="num">3</td>', '<td class="num">3</td><td class="num">4</td>', 1),
    # 単勝の欄が2つある行
    win_html().replace('<td class="odds_tan">5.0</td>', '<td class="odds_tan">1.0</td><td class="odds_tan">5.0</td>', 1),
    # 同じ馬番が2回
    win_html().replace('<td class="num">3</td>', '<td class="num">2</td>', 1),
    # 単勝の欄の中に表
    win_html().replace('<td class="odds_tan">5.0</td>', '<td class="odds_tan"><table><tr><td>5.0</td></tr></table></td>', 1),
])
def test_win_odds_rejects_wrong_structure(page):
    assert not jra.parse_win_odds(page, list(range(1, N + 1)))["ok"]


@pytest.mark.parametrize("page", [
    trio_html().replace('<th scope="row">3</th><td>16.0</td>', '<th scope="row">3</th><th>x</th><td>16.0</td>', 1),
    trio_html().replace('<th scope="row">3</th><td>16.0</td>', '<th scope="row">3</th><td>1</td><td>16.0</td>', 1),
    trio_html().replace('<caption>1-2</caption>', '<caption>1-2 参考</caption>', 1),
    trio_html().replace('<caption>1-2</caption>', '<caption>1-1</caption>', 1),
    trio_html().replace('<caption>1-2</caption>', '', 1),
    trio_html().replace('<caption>1-2</caption>', '<caption>1-2</caption><caption>1-2</caption>', 1),
    trio_html().replace('<th scope="row">3</th><td>16.0</td>', '<th scope="row">3</th><td><table><tr><td>16.0</td></tr></table></td>', 1),
    trio_html().replace('<th scope="row">3</th><td>16.0</td>', '<th scope="row">x3</th><td>16.0</td>', 1),
])
def test_trio_odds_rejects_wrong_structure(page):
    assert not jra.parse_trio_odds(page, list(range(1, N + 1)))["ok"]


def _tm(*texts):
    return "".join(f'<div class="cell time"><strong>{t}</strong></div>' for t in texts) + '<div class="cell time">発走時刻：15時40分</div>'


@pytest.mark.parametrize("text,expect", [
    (_tm("10時99分現在オッズ"), None), (_tm("24時00分現在オッズ"), None), ("<p>なし</p>", None),
    (_tm("10時49分現在オッズ", "10時50分現在オッズ"), None), (_tm("10時49分現在オッズ", "10時49分現在オッズ"), None),
    (_tm("114時25分現在オッズ"), None), (_tm("前回 10時40分現在オッズ"), None),
    ("<p>10時49分現在オッズ</p>", None),  # 決まった欄の外の時刻は読まない
    (_tm("８時５０分現在オッズ"), "08:50"), (_tm("8時50分現在オッズ"), "08:50"), (_tm("14時25分現在オッズ"), "14:25"),
])
def test_odds_time_is_checked(text, expect):
    assert jra.parse_odds_time(text) == expect


def test_card_reads_only_entry_rows_and_flags_unreadable_numbers():
    from keiba.live.wallet import card_problem
    ids = [f"H{h:04d}" for h in range(N)]
    other = '<table><tr><td class="date">2025</td><td class="horse"><div class="name"><a>過去の勝ち馬</a></div></td></tr></table>'
    rows = jra.parse_card(card_html(ids) + other)
    assert [r["horse_number"] for r in rows] == list(range(1, N + 1))  # 別の表の行は読まない
    bad = card_html(ids).replace('<td class="num">3</td>', '<td class="num">3a</td>', 1)
    nums = [r["horse_number"] for r in jra.parse_card(bad)]
    assert None in nums and "読めない" in card_problem(nums, N)


def test_pre_draw_card_makes_no_inputs(tmp_path):
    """枠順の確定前（馬番が空）の出馬表では、その時点の入力を作らない。"""
    ids = [f"H{h:04d}" for h in range(N)]
    empty = card_html(ids)
    for k in range(1, N + 1):
        empty = empty.replace(f'<td class="num">{k}</td>', '<td class="num"></td>', 1)
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path, card=empty)
    root = tmp_path / "live"
    state = json.loads((root / "state.json").read_text())
    assert (RK, "1h") not in live_tables(root, state, _snapshots(root))[2]


def test_one_bad_race_does_not_stop_others(tmp_path, monkeypatch):
    """1レースの読み取りで例外が起きても、そのレースだけ保留にして、ほかのレースは読む。"""
    from keiba.live import wallet
    paths, col = _setup(tmp_path)
    rk2 = "2025-08-30|東京|12"
    col.state["races"][rk2] = dict(col.state["races"][RK], race_number=12)
    ids = [f"H{h:04d}" for h in range(N)]
    for kind, page in (("card", card_html(ids)), ("odds_win", win_html()), ("odds_trio", trio_html() + "<!--boom-->")):
        col._save(rk2, kind, "1h", "/JRADB/x", "c", page)
    col._write_state()
    orig = jra.parse_trio_odds

    def boom(page, horses):
        if "boom" in page:
            raise ValueError("こわれたページ")
        return orig(page, horses)
    monkeypatch.setattr(jra, "parse_trio_odds", boom)
    root = tmp_path / "live"
    state = json.loads((root / "state.json").read_text())
    held = {}
    _, runners, inputs = wallet.live_tables(root, state, wallet._snapshots(root), held)
    assert (RK, "1h") in inputs and inputs[(RK, "1h")]["gate"]["pass"]
    assert not any(k[0] == rk2 for k in inputs) and "こわれたページ" in held[rk2]
    assert rk2 not in set(runners["race_key"])


def _scratch_card(scratched, n=N, other_none=()):
    """実物と同じく、取消の馬は馬番の欄が「取消」になる出馬表。"""
    ids = [f"H{h:04d}" for h in range(n)]
    html = card_html(ids)
    for k in scratched:
        html = html.replace(f'<td class="num">{k}</td>', '<td class="num"><span class="cap">取消</span></td>', 1)
    for k in other_none:
        html = html.replace(f'<td class="num">{k}</td>', '<td class="num"></td>', 1)
    return html


@pytest.mark.parametrize("scratched", [[5], [1], [N], [4, 5], [1, 2], [N - 1, N]])
def test_scratched_rows_get_their_numbers(scratched):
    from keiba.live.wallet import card_problem
    rows = jra.parse_card(_scratch_card(scratched))
    assert [r["horse_number"] for r in rows] == list(range(1, N + 1))
    assert sorted(r["horse_number"] for r in rows if r["scratch"]) == scratched
    assert all(r.get("horse_number_inferred") for r in rows if r["scratch"])
    assert card_problem([r["horse_number"] for r in rows], N) is None


def test_unreadable_non_scratched_row_is_not_inferred():
    from keiba.live.wallet import card_problem
    rows = jra.parse_card(_scratch_card([5], other_none=[6]))  # 取消の隣に、取消でないのに番号の無い行
    nums = [r["horse_number"] for r in rows]
    assert nums.count(None) == 2 and "読めない" in card_problem(nums, N)


def test_scratch_race_end_to_end(tmp_path):
    """取消の馬（番号の欄が「取消」）がいるレース：出走馬の全組と単勝がそろえば合格し、取消の馬の組は使わない。"""
    active = [k for k in range(1, N + 1) if k != 5]
    trio = '<div class="cell time"><strong>14時25分現在オッズ</strong></div>' + "".join(
        f'<table class="basic narrow-xy fuku3"><caption>{a}-{b}</caption><tbody>'
        + "".join(f'<tr><th scope="row">{c}</th><td>{"取消" if 5 in (a, b, c) else 10.0 + a + b + c}</td></tr>'
                  for c in range(b + 1, N + 1)) + "</tbody></table>"
        for a, b in combinations(range(1, N + 1), 2) if b < N)
    win = win_html(cell=lambda k: "取消" if k == 5 else 2.0 + k)
    paths, col = _setup(tmp_path, card=_scratch_card([5]), trio=trio)
    col._save(RK, "odds_win", "1h", "/JRADB/x", "c", win)
    col._write_state()
    from keiba.live.wallet import _snapshots, live_tables
    root = tmp_path / "live"
    inp = live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root))[2][(RK, "1h")]
    assert inp["scratched"] == [5] and inp["gate"]["pass"] and inp["win"]["ok"]
    assert len(inp["trio"]["odds"]) == 35 and not any("5" in k.split("-") for k in inp["trio"]["odds"])


def test_unreadable_card_row_fails_gate_with_reason(tmp_path):
    """番号の読めない行（取消でない）がある出馬表：例外で保留にせず、理由つきで不合格にする。単勝も使わない。"""
    ids = [f"H{h:04d}" for h in range(N)]
    bad = card_html(ids).replace(f'<td class="num">{N}</td>', '<td class="num">―</td>', 1)
    held = {}
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path, card=bad)
    root = tmp_path / "live"
    inp = live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root), held)[2][(RK, "1h")]
    assert not held and not inp["gate"]["pass"] and not inp["win"]["ok"] and "読めない" in inp["gate"]["card_issue"]


# ---- みにの5回目の独立検証（💚20261121-19）の指摘 ----

def test_nested_tables_reject_whole_odds_page():
    """指摘1：補足の表に入れ子にした fuku3・tanpuku を読まない（実物のオッズのページに入れ子の表は無い）。"""
    full = list(range(1, N + 1))
    page = trio_html().replace('<th scope="row">3</th><td>16.0</td>', '', 1)
    page += '<table class="note"><tr><td><table class="basic fuku3"><caption>1-2</caption><tbody><tr><th scope="row">3</th><td>16.0</td></tr></tbody></table></td></tr></table>'
    t = jra.parse_trio_odds(page, full)
    assert not t["ok"] and t["nested_table"]
    win = '<table class="note"><tr><td>' + win_html() + '</td></tr></table>'
    assert not jra.parse_win_odds(win, full)["ok"]


def test_card_page_with_second_entry_table_is_not_used(tmp_path):
    """指摘2：出馬表から5番の行を外して別の表に置いても、出馬表として使わない（理由を残す）。"""
    ids = [f"H{h:04d}" for h in range(N)]
    rows = card_html(ids).split("<tr>")
    five = next(r for r in rows if '<td class="num">5</td>' in r)
    card = "<tr>".join(r for r in rows if r is not five) + "<table><tbody><tr>" + five + "</tbody></table>"
    parsed = jra.parse_card(card)
    assert not list(parsed) and "2個" in parsed.problems[0]
    nested = card_html(ids).replace("</tbody></table>", '<tr><td><table><tr><td>x</td></tr></table></td></tr></tbody></table>')
    assert jra.parse_card(nested).problems
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path, card=card)
    root = tmp_path / "live"
    held = {}
    inputs = live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root), held)[2]
    assert (RK, "1h") not in inputs and "出馬表を使わない" in held[f"{RK}|1h"]


def test_scratch_needs_exact_marker_in_number_or_odds_cell():
    """指摘2：馬名の注記に「取消」の字があっても取消にしない。馬番の欄・人気の欄がちょうど「取消」のときだけ。"""
    from keiba.live.wallet import card_problem
    ids = [f"H{h:04d}" for h in range(N)]
    html = card_html(ids).replace('<td class="num">3</td>', '<td class="num">―</td>', 1)
    html = html.replace(">ウマ2<", ">ウマ2（出走取消前の所属）<", 1)
    rows = jra.parse_card(html)
    assert not any(r["scratch"] for r in rows) and "読めない" in card_problem([r["horse_number"] for r in rows], N)
    ok = card_html(ids).replace('<td class="num">3</td>', '<td class="num"><span class="cap">取消</span></td>', 1)
    assert [r["horse_number"] for r in jra.parse_card(ok) if r["scratch"]] == [3]


def test_duplicate_rows_count_even_when_not_numeric():
    """指摘4：有効な行と同じ馬番・同じ組の「取消」の行が並んだら、重複として不合格。"""
    full = list(range(1, N + 1))
    page = trio_html().replace('<th scope="row">3</th><td>16.0</td>', '<th scope="row">3</th><td>16.0</td></tr><tr><th scope="row">3</th><td>取消</td>', 1)
    t = jra.parse_trio_odds(page, full)
    assert not t["ok"] and t["duplicates"] == 1
    win = win_html().replace("</tbody>", '<tr><td class="num">3</td><td class="odds_tan">取消</td></tr></tbody>')
    assert not jra.parse_win_odds(win, full)["ok"]


def test_broken_index_line_does_not_stop_everything(tmp_path):
    """指摘6：snapshots.jsonl に途中で切れた行があっても、その行だけ使わずに続ける。"""
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path)
    root = tmp_path / "live"
    with open(root / "snapshots.jsonl", "a", encoding="utf-8") as f:
        f.write('{"race_key": "2025-08-30|東京|11", "kind": "odds_tr\n')
    snaps = _snapshots(root)
    assert snaps.attrs["bad_lines"] == 1
    inp = live_tables(root, json.loads((root / "state.json").read_text()), snaps)[2][(RK, "1h")]
    assert inp["gate"]["pass"]


@pytest.mark.parametrize("time", ["14時99分", "114時25分", None, "14時00分", "14時45分"])
def test_win_odds_time_is_checked_like_trio(tmp_path, time):
    """単勝のページにも三連複と同じ時刻の検査（不明・ありえない・古い・締切後）。通らなければ単勝は使わない。"""
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path)
    col._save(RK, "odds_win", "1h", "/JRADB/x", "c", win_html(time=time))
    col._write_state()
    root = tmp_path / "live"
    inp = live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root))[2][(RK, "1h")]
    assert not inp["win"]["ok"] and "単勝：" in inp["win"]["reason"]
    assert inp["gate"]["pass"]  # 三連複の判定は別


def test_win_odds_with_good_time_is_used(tmp_path):
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path)
    root = tmp_path / "live"
    inp = live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root))[2][(RK, "1h")]
    assert inp["win"]["ok"] and inp["win"]["odds_time"] == "14:25"


# ---- みにの6回目の独立検証（💚20261121-22）の指摘 ----

def test_previous_day_24h_odds_are_dated_by_fetch_day(tmp_path):
    """指摘1：前日に取った24時間前のオッズの発表時刻は、取得した日の時刻。レース当日の時刻として「締切後・古い」にしない。"""
    from keiba.live.wallet import _snapshots, live_tables
    paths, col = _setup(tmp_path, tp="24h", day=29)  # レースは 8/30 15:40。24時間前の締切は 8/29 15:40。取得 8/29 14:30・発表 14:25
    root = tmp_path / "live"
    inp = live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root))[2][(RK, "24h")]
    assert inp["gate"]["pass"], inp["gate"]["reasons"]
    assert inp["win"]["ok"]


@pytest.mark.parametrize("fetched,odds_time,ok", [
    ("2025-08-30T00:05:00+09:00", "23:58", True),   # 日付をまたいだ直後：前の日の 23:58 発表
    ("2025-08-30T00:05:00+09:00", "00:04", True),
    ("2025-08-30T00:05:00+09:00", "00:30", False),  # 取得より後の時刻 → 前の日の発表と見なすと古すぎる
    ("2025-08-30T14:30:00+09:00", "13:00", False),  # 古い
])
def test_odds_time_date_rolls_back_only_across_midnight(fetched, odds_time, ok):
    from keiba.live.wallet import time_reasons
    race = {"race_date": "2025-08-31", "post_time": "15:40"}
    assert (not time_reasons(odds_time, fetched, race, "24h")) == ok


def test_index_line_cut_before_newline_does_not_swallow_next_record(tmp_path):
    """指摘2：目録の最後の行が改行の前で切れていても、次に足す行は別の行になり、次のレースの記録が消えない。"""
    from keiba.live.wallet import _snapshots
    paths, col = _setup(tmp_path)
    root = tmp_path / "live"
    with open(root / "snapshots.jsonl", "a", encoding="utf-8") as f:
        f.write('{"race_key": "2025-08-30|東京|11", "kind": "odds_tr')  # 改行の前で切れた
    rec = col._save("2025-08-30|東京|12", "card", "1h", "/JRADB/x", "c", card_html([f"H{h:04d}" for h in range(N)]))
    snaps = _snapshots(root)
    assert snaps.attrs["bad_lines"] == 1 and rec["path"] in set(snaps["path"])
    # すでにつながってしまった行（古い版で書かれたもの）も、読める記録は拾う
    joined = '{"race_key": "x", "kind": "odds_tr' + json.dumps(rec, ensure_ascii=False) + "\n"
    (root / "snapshots.jsonl").write_text(joined, encoding="utf-8")
    snaps = _snapshots(root)
    assert list(snaps["path"]) == [rec["path"]] and snaps.attrs["bad_lines"] == 1


def test_duplicate_rows_with_extra_cells_still_count():
    """指摘3：有効な行と同じ馬番・組の「取消」の行に余分なセルがあっても、重複として数える。"""
    full = list(range(1, N + 1))
    page = trio_html().replace('<th scope="row">3</th><td>16.0</td>',
                               '<th scope="row">3</th><td>16.0</td></tr><tr><th scope="row">3</th><td>取消</td><td>9.9</td>', 1)
    t = jra.parse_trio_odds(page, full)
    assert not t["ok"] and t["duplicates"] == 1
    win = win_html().replace("</tbody>", '<tr><td class="num">3</td><td class="odds_tan">取消</td><td class="odds_tan">9.9</td></tr></tbody>')
    assert not jra.parse_win_odds(win, full)["ok"]


def test_too_few_readable_runners_leaves_reason(tmp_path):
    """提案：取消のあと読める出走馬が3頭未満なら、理由を残す。"""
    from keiba.live.wallet import _snapshots, live_tables
    ids = [f"H{h:04d}" for h in range(N)]
    card = card_html(ids)
    for k in range(3, N + 1):
        card = card.replace(f'<td class="num">{k}</td>', '<td class="num"><span class="cap">取消</span></td>', 1)
    paths, col = _setup(tmp_path, card=card)
    root = tmp_path / "live"
    held = {}
    live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root), held)
    assert "2頭" in held[f"{RK}|1h"]


@pytest.mark.parametrize("order", ["second", "first"])
def test_duplicate_in_any_number_cell_counts(order):
    """みに💚20261121-23：馬番の欄が2つある「取消」行で、どちらかの欄が現役の馬番・組と同じなら重複（順番によらない）。"""
    n = 4
    full = list(range(1, n + 1))
    cells = '<td class="num">5</td><td class="num">3</td>' if order == "second" else '<td class="num">3</td><td class="num">5</td>'
    win = win_html(n=n).replace("<tbody>", f'<tbody><tr>{cells}<td class="odds_tan">取消</td></tr>', 1)
    w = jra.parse_win_odds(win, full)
    assert not w["ok"] and "馬番の重複1" in w["problems"]
    ths = '<th scope="row">1</th><th scope="row">3</th>' if order == "second" else '<th scope="row">3</th><th scope="row">1</th>'
    trio = trio_html(n=n).replace("<caption>1-2</caption><tbody>", f"<caption>1-2</caption><tbody><tr>{ths}<td>取消</td></tr>", 1)
    t = jra.parse_trio_odds(trio, full)
    assert not t["ok"] and t["duplicates"] == 1


def test_card_row_with_two_number_cells_is_rejected():
    """同じ穴の横展開：出馬表の出走の行に馬番の欄が2つあれば、出馬表ごと使わない。"""
    ids = [f"H{h:04d}" for h in range(N)]
    bad = card_html(ids).replace('<td class="num">8</td>', '<td class="num">8</td><td class="num">3</td>', 1)
    rows = jra.parse_card(bad)
    assert rows.problems and not list(rows)


def test_odds_but_no_readable_numbers_leaves_reason(tmp_path):
    """みに提案：オッズのページがあるのに出馬表の馬番が全部空なら、枠順確定前と区別して理由を残す。"""
    from keiba.live.wallet import _snapshots, live_tables
    ids = [f"H{h:04d}" for h in range(N)]
    empty = card_html(ids)
    for k in range(1, N + 1):
        empty = empty.replace(f'<td class="num">{k}</td>', '<td class="num"></td>', 1)
    paths, col = _setup(tmp_path, card=empty)
    root = tmp_path / "live"
    held = {}
    live_tables(root, json.loads((root / "state.json").read_text()), _snapshots(root), held)
    assert "1つも読めない" in held[f"{RK}|1h"]


@pytest.mark.parametrize("fetched,odds_time,ok", [
    ("2025-08-30T23:59:30+09:00", "00:00", True),   # 時計の進み30秒が日付をまたぐ（翌日 0:00 の発表）
    ("2025-08-30T23:59:30+09:00", "00:02", False),  # 2分先は進みすぎ → 前日扱いで古い
    ("2025-08-30T14:30:00+09:00", "14:31", True),   # 同じ日の1分の進み
])
def test_clock_skew_tolerance_across_midnight(fetched, odds_time, ok):
    from keiba.live.wallet import time_reasons
    race = {"race_date": "2025-09-01", "post_time": "15:40"}
    assert (not time_reasons(odds_time, fetched, race, "24h")) == ok


NO_VOTES = '<td class="zero">票数なし</td>'


def _no_vote_page(cell=NO_VOTES, page=None):
    """1-2-3 の組の倍率の欄を cell に差し替えたページ。"""
    return (page or trio_html()).replace("<td>16.0</td>", cell, 1)


def test_no_votes_combo_counts_but_has_no_odds():
    """実物（2026-11-22 東京3R）：票数なしは組として数え、倍率は持たない。"""
    t = jra.parse_trio_odds(_no_vote_page(), list(range(1, N + 1)))
    assert t["ok"] and t["found"] == t["expected"] == 56 and t["no_votes"] == ["1-2-3"]
    assert "1-2-3" not in t["odds"] and len(t["odds"]) == 55 and t["unreadable"] == 0
    assert jra.parse_trio_odds(_no_vote_page(), N)["ok"]  # 頭数で照らすときも同じ


@pytest.mark.parametrize("cell", [
    "<td>票数なし</td>", '<td class="zero"></td>', '<td class="zero">---</td>', '<td class="zero">取消</td>',
    '<td class="zero">票数なし（予定）</td>', '<td class="nonzero">票数なし</td>', '<td class="cancel">票数なし</td>',
    '<td class="zero">票数なし</td><td>16.0</td>', '<td class="zero"><table><tr><td>票数なし</td></tr></table></td>',
])
def test_only_exact_no_votes_cell_is_accepted(cell):
    assert not jra.parse_trio_odds(_no_vote_page(cell), list(range(1, N + 1)))["ok"]


@pytest.mark.parametrize("first", [True, False])
def test_no_votes_does_not_hide_duplicates(first):
    rows = [f'<tr><th scope="row">3</th>{NO_VOTES}</tr>', '<tr><th scope="row">3</th><td>16.0</td></tr>']
    page = trio_html().replace(rows[1], "".join(rows if first else rows[::-1]), 1)
    t = jra.parse_trio_odds(page, list(range(1, N + 1)))
    assert not t["ok"] and t["duplicates"] == 1


def test_no_votes_does_not_hide_wrong_roster():
    assert not jra.parse_trio_odds(_no_vote_page(), [1, 2, 4, 5, 6, 7, 8])["ok"]  # 取消の馬の組が票数なしでも余分
    assert not jra.parse_trio_odds(_no_vote_page(), list(range(1, N + 2)))["ok"]


@pytest.mark.parametrize("keep", [0, 1, 5])
def test_mostly_no_votes_page_is_rejected(tmp_path, keep):
    """倍率の逆数の合計が1未満になるほど票数なしなら、倍率が欠けているので使わない。"""
    import re
    count = iter(range(1000))
    page = re.sub(r"<td>[\d.]+</td>", lambda m: m.group(0) if next(count) < keep else NO_VOTES, trio_html())
    assert not jra.parse_trio_odds(page, list(range(1, N + 1)))["ok"]
    r = _gate(tmp_path, trio=page)
    assert not r["pass"] and "1未満" in r["reasons"]


def test_no_votes_gate_records_count(tmp_path):
    r = _gate(tmp_path, trio=_no_vote_page())
    assert r["pass"] and r["trio"] == "56/56" and r["no_votes"] == 1


def test_no_votes_combo_is_never_bought(tmp_path, monkeypatch):
    """学習の財布はほかの組を使い、票数なしの組は買わない。統合は扱いが決まるまで参考表示。"""
    import re
    from keiba.ledger import Ledger
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card",
                        lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)])
    flat = re.sub(r"<td>[\d.]+</td>", "<td>45.0</td>", trio_html())  # 期待値が1を超える組が出る倍率
    combo = "1-2-3"
    paths, _ = _setup(tmp_path, trio=flat.replace("<td>45.0</td>", NO_VOTES, 1))
    run_live(paths)
    led = Ledger(paths.ledger_path, read_only=True)
    p = {x["method"]: x for x in led.predictions()}
    led.close()
    stakes = [a["stakes"] for m in ("ml_lgbm", "ml_market") for a in p[m]["allocation"].values()]
    assert p["ml_lgbm"]["odds_gate"]["pass"] and any(stakes) and all(combo not in s for s in stakes)
    assert combo in p["ml_lgbm"]["trio_probs_full"]  # 予想の確率は全組のまま
    for m in ("int_market", "int_market_ml", "int_market_ml_mam"):
        assert not p[m]["formal_probability"] and p[m]["trio_probs_full"] is None
        assert all(a["status"] == "not_allocated" and not a["stakes"] for a in p[m]["allocation"].values())
        assert any("票数なし" in r for r in p[m]["reasons"])


# ---- みに💚20261122-2（PR #18）の指摘 ----
def _partly_no_votes(keep, value):
    import re
    count = iter(range(1000))
    return re.sub(r"<td>[\d.]+</td>", lambda m: f"<td>{value}</td>" if next(count) < keep else NO_VOTES, trio_html())


@pytest.mark.parametrize("value", ["0." + "0" * 309 + "1", "0.0000001", "0.5", "0.99"])
def test_odds_below_one_are_rejected(value):
    """重大1：極小の倍率（逆数が無限大になる）と、1倍未満の倍率は読まない。単勝も同じ。"""
    full = list(range(1, N + 1))
    t = jra.parse_trio_odds(_partly_no_votes(1, value), full)
    assert not t["ok"] and not t["odds"] and t["unreadable"] == 1
    assert not jra.parse_trio_odds(trio_html().replace("<td>16.0</td>", f"<td>{value}</td>", 1), full)["ok"]
    assert not jra.parse_win_odds(win_html(cell=lambda k: value if k == 1 else 2.0 + k), full)["ok"]


def test_payback_floor_odds_are_still_read():
    from keiba.allocate import allocate
    assert jra._num("1.0") == 1.0 and jra._num("99999.9") == 99999.9
    for m in ("equal_payout", "ev_weighted", "kelly"):
        assert allocate({"1-2-3": 1.0}, {"1-2-3": 1.0}, m).total == 0


def test_tiny_odds_race_does_not_stop_next_race(tmp_path, monkeypatch):
    """重大1：異常な倍率のレースがあっても、その回のほかのレースの予想・配分は続く。"""
    from keiba.ledger import Ledger
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card",
                        lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)])
    paths, col = _setup(tmp_path, trio=_partly_no_votes(1, "0." + "0" * 309 + "1"))
    rk2 = "2025-08-30|東京|12"
    col.state["races"][rk2] = dict(col.state["races"][RK], race_number=12, post_time="15:42")
    for kind, page in (("card", card_html([f"H{h:04d}" for h in range(N)])), ("odds_win", win_html()), ("odds_trio", trio_html())):
        col._save(rk2, kind, "1h", "/JRADB/x", "c", page)
    col._write_state()
    run_live(paths)
    led = Ledger(paths.ledger_path, read_only=True)
    got = {(p["race_key"], p["method"]): p for p in led.predictions()}
    led.close()
    assert len([k for k in got if k[0] == RK]) == len([k for k in got if k[0] == rk2]) == 11
    assert not got[(RK, "ml_lgbm")]["odds_gate"]["pass"] and got[(rk2, "ml_lgbm")]["odds_gate"]["pass"]


@pytest.mark.parametrize("cell", [
    '<td class="zero cancel">票数なし</td>', '<td class="warning zero">票数なし</td>',
    '<td class="warning&nbsp;zero">票数なし</td>', '<td class="zero&nbsp;">票数なし</td>', '<td class="ZERO">票数なし</td>',
    '<td class="zero">　票数なし　</td>', '<td class="zero">&nbsp;票数なし</td>', '<td class="zero">票数 なし</td>',
    '<td class="zero"><span>票数なし</span></td>', '<td class="zero">票数なし<br></td>',
    '<td class="zero">16.0</td>', '<td class="zero cancel">16.0</td>',
])
def test_no_votes_marker_is_exact_raw_form(cell):
    """要修正1：class と文字は生の形でぴったり。印と倍率が食い違う欄も読まない。"""
    assert not jra.parse_trio_odds(_no_vote_page(cell), list(range(1, N + 1)))["ok"]


def test_no_votes_allows_html_whitespace_only():
    assert jra.parse_trio_odds(_no_vote_page('<td class="zero">\r\n  票数なし\t</td>'), list(range(1, N + 1)))["ok"]


@pytest.mark.parametrize("keep,value,ok", [
    (49, "49.0", True), (49, "48.9", True), (49, "49.1", False), (16, "16.0", True), (7, "7.0", True),
    (3, "3.0", True), (55, "55.0", True), (55, "55.1", False), (1, "1.0", True),
])
def test_inverse_sum_boundary_is_exact(keep, value, ok):
    """要修正2：逆数の合計がちょうど1なら合格（小数の誤差で落とさない）。"""
    t = jra.parse_trio_odds(_partly_no_votes(keep, value), list(range(1, N + 1)))
    assert t["ok"] == ok and t["implausible"] != ok


def test_gate_keeps_both_reasons(tmp_path):
    """提案1：逆数の合計1未満と、重複・不足が同時にあれば理由は両方。片方だけなら1つ。"""
    page = _partly_no_votes(49, "60.0")
    r = _gate(tmp_path / "a", trio=page)
    assert "1未満" in r["reasons"] and "重複" not in r["reasons"]
    three = '<tr><th scope="row">3</th><td>60.0</td></tr>'
    four = '<tr><th scope="row">4</th><td>60.0</td></tr>'
    r = _gate(tmp_path / "b", trio=page.replace(three, three + three, 1).replace(four, "", 1))
    assert not r["pass"] and all(w in r["reasons"] for w in ("1未満", "重複1", "不足1"))


def test_reference_integration_is_not_scored(tmp_path, monkeypatch):
    """要修正3：参考表示の統合は月次の当たり具合に数えない。"""
    from keiba.review import build_review
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card",
                        lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)])
    paths, col = _setup(tmp_path, trio=_no_vote_page())
    rk2 = "2025-08-30|東京|12"
    col.state["races"][rk2] = dict(col.state["races"][RK], race_number=12, post_time="15:42")
    for kind, page in (("card", card_html([f"H{h:04d}" for h in range(N)])), ("odds_win", win_html()), ("odds_trio", trio_html())):
        col._save(rk2, kind, "1h", "/JRADB/x", "c", page)
    col._write_state()
    run_live(paths)
    for rk in (RK, rk2):
        col._save(rk, "result", "result", "/JRADB/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
    run_live(paths)
    acc = {a["method"]: a["races"] for a in build_review(paths, "2025-08")["accuracy"]}
    assert acc["ml_lgbm"] == 2 and acc["int_market"] == acc["int_market_ml"] == 1
    assert "int_market_ml_mam" not in acc  # まむ抜きの参考予想も、まむ入りの成績にしない


@pytest.mark.parametrize("ps,ok", [
    ([float("nan")], False), ([float("inf")], False), ([-0.1, 0.3], False), ([0.0, 0.0], False), ([1e308, 1e308], False),
    (["0.3"], False), ([True], False), ([10 ** 400], False), ([0.08, 0.05], True), ([8, 5], True), ([0.0, 0.2], True),
])
def test_mam_probability_must_be_finite(tmp_path, monkeypatch, ps, ok):
    """同じ穴：まむの確率の非数・無限大・桁あふれで、統合の計算が止まらない。"""
    from keiba.live.mam import import_predictions
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card",
                        lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)])
    paths, _ = _setup(tmp_path)
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"schema": "keiba-mam-prediction-v1", "race_key": RK, "time_point": "1h",
                             "generated_at": "2025-08-30T14:35:00+09:00",
                             "trios": [{"combo": c, "p": p} for c, p in zip(("1-2-3", "1-2-4"), ps)]}))
    assert (import_predictions(paths, f)["accepted"] == 1) == ok
    if ok:
        run_live(paths)


# ---- みに💚20261122-3（PR #18）の指摘
RK2 = "2025-08-30|東京|12"


def _two_races(tmp_path, monkeypatch, trio=None):
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card",
                        lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)])
    paths, col = _setup(tmp_path, trio=trio)
    col.state["races"][RK2] = dict(col.state["races"][RK], race_number=12, post_time="15:42")
    for kind, page in (("card", card_html([f"H{h:04d}" for h in range(N)])), ("odds_win", win_html()), ("odds_trio", trio_html())):
        col._save(RK2, kind, "1h", "/JRADB/x", "c", page)
    col._write_state()
    return paths, col


def _stored_mam(paths, **change):
    """検査のゆるかった版が台帳に入れた、まむの予想。"""
    from keiba.ledger import Ledger
    payload = {"race_date": "2025-08-30", "method_kind": "text_ai", "horses": [{"n": n, "rank": None} for n in range(1, N + 1)],
               "trios": [{"combo": "1-2-3", "p": 1.0}], "trios_all": {"1-2-3": 1.0}, "complete": False, "confidence": "Z",
               "allocation": {a: {"status": "allocated", "stakes": {"1-2-3": 100}, "total": 100}
                              for a in ("equal_payout", "ev_weighted", "kelly")},
               "period": "live", "issued_horses": list(range(1, N + 1)), **change}
    led = Ledger(paths.ledger_path)
    led.add_prediction(RK, "1h", "text_ai", "live-mam-v1", payload, "i", "mam", "old")
    led.commit()
    led.close()


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize("probs", [
    {"1-2-3": NAN}, {"1-2-3": INF}, {"1-2-3": -INF}, {"1-2-3": -0.2, "1-2-4": 1.2}, {"1-2-3": 7.0}, {"1-2-3": 0.0},
    {"1-2-3": "0.5"}, {"1-2-3": None}, {"1-2-3": True}, {"1-2-3": [1]}, {"1-2-3": 5e-324, "1-2-4": 1.0},
    {"1-2-3": 1.0, "1-2-4": NAN}, {"1-2-3": 10 ** 400}, {"1-2-99": 1.0}, {"3-2-1": 1.0}, {"1-2-3-4": 1.0}, {"x": 1.0},
    [["1-2-3", 1.0]], [], "NaN", 0,
])
@pytest.mark.parametrize("no_votes", [True, False])
def test_broken_saved_mam_probs_are_not_pooled(tmp_path, monkeypatch, probs, no_votes):
    """重大1：台帳に残った壊れた確率は統合に入れず、そのレースも次のレースも予想が保存される。"""
    from keiba.ledger import Ledger
    paths, _ = _two_races(tmp_path, monkeypatch, trio=_no_vote_page() if no_votes else None)
    _stored_mam(paths, trios_all=probs, complete=True)
    assert run_live(paths)["predicted"] == 22
    led = Ledger(paths.ledger_path, read_only=True)
    got = {(p["race_key"], p["method"]): p for p in led.predictions()}
    led.close()
    it = got[(RK, "int_market_ml_mam")]
    assert "mam" not in it["inputs_used"] and not it["formal_probability"]
    assert any("保存済みの確率を使えない" in r for r in it["reasons"])
    assert all(not a["stakes"] for a in it["allocation"].values())
    assert got[(RK2, "ml_lgbm")]["odds_gate"]["pass"]


@pytest.mark.parametrize("probs", [{"1-2-3": 1.0}, {"1-2-3": 1, "1-2-4": 0}, {"1-2-3": 1e-7, "1-2-4": 0.9999999}])
def test_good_saved_mam_probs_are_still_pooled(tmp_path, monkeypatch, probs):
    from keiba.ledger import Ledger
    paths, _ = _two_races(tmp_path, monkeypatch)
    _stored_mam(paths, trios_all=probs)
    assert run_live(paths)["predicted"] == 22
    led = Ledger(paths.ledger_path, read_only=True)
    it = next(p for p in led.predictions() if p["race_key"] == RK and p["method"] == "int_market_ml_mam")
    led.close()
    assert "mam" in it["inputs_used"]


@pytest.mark.parametrize("small,formal", [(0.0, True), (4e-8, True), (5.1e-8, True), (0.01, True), (None, False)])
def test_accepted_mam_probs_pass_saved_check(tmp_path, monkeypatch, small, formal):
    """受付が保存した値（7桁丸めの端を含む）は、保存値の検査で落ちない。順位だけの予想も今までどおり。"""
    from itertools import combinations
    from keiba.ledger import Ledger
    from keiba.live.mam import export_packets, import_predictions
    paths, _ = _two_races(tmp_path, monkeypatch)
    export_packets(paths)
    combos = ["-".join(map(str, c)) for c in combinations(range(1, N + 1), 3)]
    body = {"ranking": [1, 2, 3]} if small is None else {
        "trios": [{"combo": c, "p": 1.0 if i == 0 else small} for i, c in enumerate(combos)]}
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"schema": "keiba-mam-prediction-v1", "race_key": RK, "time_point": "1h",
                             "generated_at": "2025-08-30T14:35:00+09:00", **body}))
    assert import_predictions(paths, f)["accepted"] == 1
    run_live(paths)
    led = Ledger(paths.ledger_path, read_only=True)
    it = next(p for p in led.predictions() if p["race_key"] == RK and p["method"] == "int_market_ml_mam")
    led.close()
    assert "mam" in it["inputs_used"] and it["formal_probability"] == formal
    assert not any("保存済みの確率を使えない" in r for r in it["reasons"])


@pytest.mark.parametrize("text", [
    "0.99999999999999999", "1.000000000000001", "999999.90000000001", "999,999.91", "2.00000000000000001", "2.50", "2.",
    "１６.０", "١٦", "1e2", "00000001.0", "0.9", "1,000,000.0",
    "\u00a016.0", "16.0\u3000", "16 .0", "16.0\n0",
])
def test_odds_digits_are_explicit(text):
    """要修正1：小数1桁までの半角だけ。長い小数を丸めて境目を越えさせない（単勝も同じ）。"""
    full = list(range(1, N + 1))
    assert jra._num(text) is None
    assert not jra.parse_trio_odds(trio_html().replace("<td>16.0</td>", f"<td>{text}</td>", 1), full)["ok"]
    assert not jra.parse_win_odds(win_html(cell=lambda k: text if k == 1 else 2.0 + k), full)["ok"]


@pytest.mark.parametrize("text,value", [("1", 1.0), ("1.0", 1.0), ("2.4", 2.4), ("99,999.9", 99999.9), ("99999.9", 99999.9), ("999,999.9", 999999.9)])
def test_displayed_odds_round_trip_exactly(text, value):
    from fractions import Fraction
    assert jra._num(text) == value and Fraction(str(jra._num(text))) == Fraction(text.replace(",", ""))


# ---- みに💚20261124-1（PR #18）の指摘
def _combos(n):
    from itertools import combinations
    return ["-".join(map(str, c)) for c in combinations(range(1, n + 1), 3)]


def _mam_row(paths, probs, complete, **change):
    from keiba.ledger import Ledger
    _stored_mam(paths, trios_all=probs, complete=complete, **change)
    run_live(paths)
    led = Ledger(paths.ledger_path, read_only=True)
    it = next(p for p in led.predictions() if p["race_key"] == RK and p["method"] == "int_market_ml_mam")
    led.close()
    return it


def test_mam_from_a_smaller_roster_is_not_formal(tmp_path, monkeypatch):
    """重大1：出走馬が少ない時に全組そろったまむの予想は、今の出走馬では全組ではない。正式にせず配分しない。"""
    paths, _ = _two_races(tmp_path, monkeypatch)
    small = _combos(N - 1)
    it = _mam_row(paths, {c: (0.8 if i == 0 else round(0.2 / (len(small) - 1), 7)) for i, c in enumerate(small)}, True,
                  horses=[{"n": n, "rank": None} for n in range(1, N)], issued_horses=list(range(1, N)))
    assert "mam" in it["inputs_used"] and not it["formal_probability"] and it["trio_probs_full"] is None
    assert any("予想に使った入力と今の出走馬が違う" in r for r in it["reasons"])
    assert all(not a["stakes"] for a in it["allocation"].values())


@pytest.mark.parametrize("label,probs,complete", [
    ("合計56", {c: 1.0 for c in _combos(N)}, True),
    ("合計が1より大きい", {c: (0.9 if i == 0 else 0.125) for i, c in enumerate(_combos(N))}, True),
    ("合計が1より小さい", {c: 0.001 for c in _combos(N)}, True),
    ("確率なしでそろい印", None, True),
    ("空でそろい印", {}, True),
])
def test_saved_mam_mass_and_completeness_are_checked(tmp_path, monkeypatch, label, probs, complete):
    """要修正1：保存済みの確率の合計・そろい印の矛盾を通さない。"""
    paths, _ = _two_races(tmp_path, monkeypatch)
    it = _mam_row(paths, probs, complete)
    assert not it["formal_probability"] and it["trio_probs_full"] is None, label
    assert not any("出走馬が違う" in r for r in it["reasons"]), label
    if label.startswith("合計"):
        assert any("合計が1でない" in r for r in it["reasons"]), label
    assert all(not a["stakes"] for a in it["allocation"].values())


def test_full_seven_digit_mam_is_still_formal(tmp_path, monkeypatch):
    """正常対照：今の全組・7桁丸めの合計1（丸めの誤差あり）は、今までどおり正式。"""
    paths, _ = _two_races(tmp_path, monkeypatch)
    full = _combos(N)
    probs = {c: round(1 / len(full), 7) for c in full}
    assert sum(probs.values()) != 1
    it = _mam_row(paths, probs, True)
    assert "mam" in it["inputs_used"] and it["formal_probability"]


# ---- みに💚20261125-2（PR #18）の指摘
def _uniform(keys):
    return {c: round(1 / len(keys), 7) for c in keys}


@pytest.mark.parametrize("saved", [tuple(range(1, 9)), tuple(range(1, 8)), (1, 2, 3, 4, 5, 6, 8)])
@pytest.mark.parametrize("now", [tuple(range(1, 9)), tuple(range(1, 8)), (1, 2, 3, 4, 5, 6, 8)])
@pytest.mark.parametrize("keys", ["all8", "all7", "all7b", "one"])
def test_mam_formal_only_with_same_roster_and_all_combos(saved, now, keys):
    """重大：正式は「予想した時と今の出走馬が同じ」かつ「今の全組がそろう」ときだけ（頭数が増えても減っても）。"""
    from itertools import combinations
    from keiba.live.wallet import _mam_use
    combos = lambda r: ["-".join(map(str, c)) for c in combinations(r, 3)]
    k = {"all8": combos(range(1, 9)), "all7": combos(range(1, 8)), "all7b": combos((1, 2, 3, 4, 5, 6, 8)),
         "one": ["1-2-3"]}[keys]
    probs = _uniform(k)
    mp = {"issued_horses": list(saved), "trios_all": probs, "complete": True}  # 印が True でも、出走馬や組が違えば正式にしない
    use, ok, why = _mam_use(mp, list(now))
    assert use == (set(k) <= set(combos(now)))
    assert ok == (saved == now and set(k) == set(combos(now)))
    assert (why is None) == ok


def test_partial_mam_does_not_become_formal_after_a_scratch(tmp_path, monkeypatch):
    """重大：8頭の時の一部の予想（1〜7の35組）は、8番が取り消されて7頭になっても正式にしない。"""
    from keiba.ledger import Ledger
    paths, col = _two_races(tmp_path, monkeypatch)
    seven = _combos(N - 1)
    _stored_mam(paths, trios_all=_uniform(seven), complete=False)
    col._save(RK, "card", "1h", "/JRADB/x", "c", _scratch_card([N]))
    col._save(RK, "odds_win", "1h", "/JRADB/x", "c", win_html(n=N - 1))
    col._save(RK, "odds_trio", "1h", "/JRADB/x", "c", trio_html(n=N - 1))
    col._write_state()
    run_live(paths)
    led = Ledger(paths.ledger_path, read_only=True)
    it = next(p for p in led.predictions() if p["race_key"] == RK and p["method"] == "int_market_ml_mam")
    led.close()
    assert it["odds_gate"]["pass"], it["odds_gate"]["reasons"]
    assert not it["formal_probability"] and any("予想に使った入力と今の出走馬が違う" in r for r in it["reasons"])
    assert all(not a["stakes"] for a in it["allocation"].values())


def test_mam_from_the_same_seven_is_formal(tmp_path, monkeypatch):
    """正常対照：最初から7頭で、7頭の全組がそろった予想は正式。"""
    from keiba.ledger import Ledger
    paths, col = _two_races(tmp_path, monkeypatch)
    col._save(RK, "card", "1h", "/JRADB/x", "c", _scratch_card([N]))
    col._save(RK, "odds_win", "1h", "/JRADB/x", "c", win_html(n=N - 1))
    col._save(RK, "odds_trio", "1h", "/JRADB/x", "c", trio_html(n=N - 1))
    col._write_state()
    _stored_mam(paths, trios_all=_uniform(_combos(N - 1)), complete=True, horses=[{"n": n, "rank": None} for n in range(1, N)],
                issued_horses=list(range(1, N)))
    run_live(paths)
    led = Ledger(paths.ledger_path, read_only=True)
    it = next(p for p in led.predictions() if p["race_key"] == RK and p["method"] == "int_market_ml_mam")
    led.close()
    assert it["odds_gate"]["pass"] and it["formal_probability"] and "mam" in it["inputs_used"]


@pytest.mark.parametrize("issued", [None, "1-8", [], [None], ["1"], [True], [1.0, 2, 3, 4, 5, 6, 7, 8],
                                    [1, 1, 2, 3, 4, 5, 6, 7], list(range(1, 10)), list(range(1, 8))])
def test_broken_or_other_issued_roster_is_reference_not_a_crash(issued):
    """渡した入力の出走馬が分からない・壊れている・違うときは、止まらずに参考表示（正式にしない）。"""
    from keiba.live.wallet import _mam_use
    use, ok, why = _mam_use({"issued_horses": issued, "trios_all": _uniform(_combos(N)), "complete": True}, list(range(1, N + 1)))
    assert use and not ok and ("出走馬が分からない" in why or "出走馬が違う" in why)


# ---- みに💚20261125-4（PR #18）の指摘
def _scratch_eight(col, tp="1h"):
    col._save(RK, "card", tp, "/JRADB/x", "c", _scratch_card([N]))
    col._save(RK, "odds_win", tp, "/JRADB/x", "c", win_html(n=N - 1))
    col._save(RK, "odds_trio", tp, "/JRADB/x", "c", trio_html(n=N - 1))
    col._write_state()


def _int_rows(paths):
    from keiba.ledger import Ledger
    led = Ledger(paths.ledger_path, read_only=True)
    rows = {(p["race_key"], p["time_point"], p["method"]): p for p in led.predictions()}
    led.close()
    return rows


def test_answer_to_an_eight_horse_packet_accepted_after_a_scratch_is_not_formal(tmp_path, monkeypatch):
    """重大①：8頭の入力に1〜7の35組だけ答えた予想を、8番取消の取得の後に受け付けても正式にしない。"""
    from keiba.live.mam import export_packets, import_predictions
    paths, col = _two_races(tmp_path, monkeypatch)
    export_packets(paths)                      # まむに渡したのは8頭の入力
    _scratch_eight(col)                        # その後に8番取消を取得
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"schema": "keiba-mam-prediction-v1", "race_key": RK, "time_point": "1h",
                             "generated_at": "2025-08-30T14:35:00+09:00",
                             "trios": [{"combo": c, "p": 1 / 35} for c in _combos(N - 1)]}))
    assert import_predictions(paths, f)["accepted"] == 1
    run_live(paths)
    rows = _int_rows(paths)
    assert rows[(RK, "1h", "text_ai")]["issued_horses"] == list(range(1, N + 1))
    it = rows[(RK, "1h", "int_market_ml_mam")]
    assert it["odds_gate"]["pass"] and not it["formal_probability"]
    assert any("予想に使った入力と今の出走馬が違う" in r for r in it["reasons"])
    assert all(not a["stakes"] for a in it["allocation"].values())


def test_answer_to_a_seven_horse_packet_is_formal(tmp_path, monkeypatch):
    """正常対照：取消の後に渡した7頭の入力へ、7頭の全組で答えた予想は正式。"""
    from keiba.live.mam import export_packets, import_predictions
    paths, col = _two_races(tmp_path, monkeypatch)
    _scratch_eight(col)
    export_packets(paths)
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"schema": "keiba-mam-prediction-v1", "race_key": RK, "time_point": "1h",
                             "generated_at": "2025-08-30T14:35:00+09:00",
                             "trios": [{"combo": c, "p": 1 / 35} for c in _combos(N - 1)]}))
    assert import_predictions(paths, f)["accepted"] == 1
    run_live(paths)
    it = _int_rows(paths)[(RK, "1h", "int_market_ml_mam")]
    assert it["formal_probability"] and "mam" in it["inputs_used"]


@pytest.mark.parametrize("packet", ["missing", "broken", "other_race", "other_time", "duplicate", "text_numbers", "empty"])
def test_issued_roster_is_unknown_unless_the_packet_matches(tmp_path, monkeypatch, packet):
    """渡した入力が無い・壊れている・別のレースや時点のものなら、出走馬は分からない（正式にしない）。"""
    from keiba.live.mam import export_packets, issued_roster, _packet_path
    paths, _ = _two_races(tmp_path, monkeypatch)
    export_packets(paths)
    f = _packet_path(paths, RK, "1h")
    body = json.loads(f.read_text(encoding="utf-8"))
    assert issued_roster(paths, RK, "1h") == list(range(1, N + 1))
    if packet == "missing":
        f.unlink()
    elif packet == "broken":
        f.write_text(f.read_text(encoding="utf-8")[:27], encoding="utf-8")
    else:
        change = {"other_race": {"race_key": RK2}, "other_time": {"time_point": "15m"},
                  "duplicate": {"horses": body["horses"] + body["horses"][:1]},
                  "text_numbers": {"horses": [dict(h, horse_number=str(h["horse_number"])) for h in body["horses"]]},
                  "empty": {"horses": []}}[packet]
        f.write_text(json.dumps({**body, **change}, ensure_ascii=False), encoding="utf-8")
    assert issued_roster(paths, RK, "1h") is None


def test_later_scratch_in_learning_rows_keeps_integration_reference(tmp_path, monkeypatch):
    """重大②：15分前に分かった取消で学習の出走馬が7頭になったら、8頭の1時間前の統合は正式にしない。"""
    paths, col = _two_races(tmp_path, monkeypatch)
    _stored_mam(paths, trios_all=_uniform(_combos(N)), complete=True)
    from datetime import datetime
    from keiba.live.collector import JST
    col.now = lambda: datetime(2025, 8, 30, 15, 20, tzinfo=JST)  # 1時間前の締切（14:40）の後に、15分前の出馬表で取消が分かる
    _scratch_eight(col, "15m")
    run_live(paths)
    rows = _int_rows(paths)
    for m in ("int_market_ml", "int_market_ml_mam"):
        it = rows[(RK, "1h", m)]
        assert it["odds_gate"]["pass"] and not it["formal_probability"], m
        assert any(r.startswith("学習：") for r in it["reasons"]), m
        assert all(not a["stakes"] for a in it["allocation"].values()), m
    assert rows[(RK, "1h", "int_market")]["formal_probability"]  # 市場だけの統合は学習を使わない


def test_mass_reason_shows_the_difference():
    """要修正：合計で外した理由に、1からのずれが見える桁で出す。"""
    from keiba.live.wallet import _mam_use
    use, ok, why = _mam_use({"issued_horses": [1, 2, 3, 4], "trios_all": {"1-2-3": 0.5000001, "1-2-4": 0.5000001}}, [1, 2, 3, 4])
    assert not use and not ok and "1.0000002" in why


@pytest.mark.parametrize("flag", ["false", "False", "true", 1, 0, [True], {"flag": True}, None, False])
def test_complete_flag_must_be_boolean_true(flag):
    """提案：全組の印が真偽値の True でない保存行は、確率がそろっていても正式にしない。"""
    from keiba.live.wallet import _mam_use
    row = {"issued_horses": list(range(1, N + 1)), "trios_all": _uniform(_combos(N)), "complete": flag}
    use, ok, why = _mam_use(row, list(range(1, N + 1)))
    assert use and not ok and "真偽値" in why
    assert _mam_use({**row, "complete": True}, list(range(1, N + 1))) == (True, True, None)
