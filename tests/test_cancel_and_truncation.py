"""受け入れ再検証（💚20261121-24）の反例：途中で切れた結果のページ・JRAのレース取りやめ・比較の複数組・クラス・取消の印。"""
import json
from datetime import datetime

import pytest

from keiba.live import jra
from keiba.live.collector import JST, Collector, cancel_notice, cancelled_result, load_cancellations, result_issue
from keiba.settle import result_problem
from tests.test_live_wallet import RK, card_html, result_html

FULL = result_html([3, 1, 2, 4, 5, 6, 7, 8])      # 三連複 1-2-3・4,560円


def empty_result(rk: str) -> str:
    """JRAが取りやめたレースに出す、見出しだけの空の結果ページ（2026-02-07 東京8Rの実物の形）。"""
    d, venue, num = rk.split("|")
    y, m, day = d.split("-")
    return ('<html><body><h1><span class="inner"><span class="txt">レース結果'
            f'<span class="opt">{int(y)}年{int(m)}月{int(day)}日（土曜）1回{venue}3日 {int(num)}レース</span></span></span></h1>'
            '</body></html>')


def cut_in_payout(page: str) -> str:
    """払戻の数字の途中（4,5）で切れたページ。"""
    return page[:page.index("4,560円") + 3]


# ---------------- 1. 途中で切れた結果のページ ----------------

def test_truncated_result_page_is_not_used():
    cut = cut_in_payout(FULL)
    assert not jra.page_complete(cut) and jra.page_complete(FULL)
    assert jra.parse_result(cut) == {"rows": [], "trio_payouts": [], "page_incomplete": True}
    assert result_issue(cut, 8) == "結果のページが最後まで届いていない"
    assert result_issue(FULL, 8) is None


def test_payout_under_100_yen_is_held():
    finished = [(3, 1), (1, 2), (2, 3), (4, 4)]
    assert "100円未満" in result_problem(finished, 4, set(), {"1-2-3": 45}, 4, 4, [3, 1, 2, 4])
    assert result_problem(finished, 4, set(), {"1-2-3": 100}, 4, 4, [3, 1, 2, 4]) is None   # 元返しは使う


def test_collector_refetches_truncated_page(tmp_path, monkeypatch):
    from keiba.live import collector as c
    rk = "2026-11-22|東京|1"
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 11, 22, 11, 0, tzinfo=JST))
    col.state["races"][rk] = {"race_date": "2026-11-22", "venue": "東京", "race_number": 1, "post_time": "10:00",
                              "field_size": 8, "list_cname": "L", "result_links": [("/JRADB/r", "R")]}
    pages = {"R": cut_in_payout(FULL)}

    class Client:
        count = 0

        def get(self, path, cname):
            return pages.get(cname, "")
    col.client = Client()
    monkeypatch.setattr(c.jra, "parse_race_list", lambda page: {"races": []})
    saved = col.capture(rk, "result")
    assert saved[-1]["incomplete"] == "結果のページが最後まで届いていない" and f"{rk}|result" not in col.state["taken"]
    pages["R"] = FULL
    col.capture(rk, "result")
    assert f"{rk}|result" in col.state["taken"]


def _wallet_setup(tmp_path):
    from tests.test_live_wallet import _setup
    orig = jra.parse_card
    jra.parse_card = lambda page: [dict(c, horse_id=f"H{int(c['horse_number']) - 1:04d}") for c in orig(page)]
    return orig, _setup(tmp_path)


def _settlements(paths):
    from keiba.ledger import Ledger
    led = Ledger(paths.ledger_path, read_only=True)
    out = list(led.settlements())
    led.close()
    return out


def test_wallet_waits_for_full_page_then_settles_right_amount(tmp_path):
    from keiba.live.wallet import run_live
    orig, (paths, col) = _wallet_setup(tmp_path)
    try:
        run_live(paths)
        col._save(RK, "result", "result", "/JRADB/x", "c", cut_in_payout(FULL))
        assert run_live(paths)["settled"] == 0                     # 途中のページでは確定しない
        col._save(RK, "result", "result", "/JRADB/x", "c2", FULL)
        assert run_live(paths)["settled"] == 3 * 5
        rows = _settlements(paths)
        hits = [s for s in rows if s.get("hits")]
        assert hits and all(s["returned"] >= 4560 for s in hits)          # 4,560円（100円あたり）で精算。45円などにしない
        assert not [s for s in rows if 0 < s["returned"] < 100]
    finally:
        jra.parse_card = orig


def test_app_does_not_show_truncated_result(tmp_path):
    from keiba.app.data import Source
    from keiba.config import Paths
    paths = Paths(data_dir=tmp_path / "data")
    live = paths.data_dir / "live"
    (live / "snapshots").mkdir(parents=True)
    rk = "2026-11-22|京都|11"
    (live / "state.json").write_text(json.dumps({"races": {rk: {"race_date": "2026-11-22", "venue": "京都", "race_number": 11,
                                                                "post_time": "15:40", "field_size": 8}}}, ensure_ascii=False))
    (live / "snapshots" / "r.html").write_text(cut_in_payout(FULL))
    (live / "snapshots.jsonl").write_text(json.dumps({"race_key": rk, "kind": "result", "time_point": "result",
                                                      "path": "snapshots/r.html", "fetched_at": "2026-11-22T16:20:00+09:00"},
                                                     ensure_ascii=False) + "\n")
    assert rk not in Source(paths, "live").results                # 結果待ちのまま（95円などで確定しない）


# ---------------- 2. JRAのレース取りやめ ----------------

NEWS = """<html><body><ul class="news_line_list">
<li class="dento pickup"><div class="news_line"><a href="/news/202602/020708.html"><div class="icon"><p>馬場関連</p></div>
<div class="txt">本日【2月7日（土曜）】の東京競馬は第8レース以降を取りやめといたします</div></a></div></li>
<li class="other pickup"><div class="news_line"><a href="/news/202602/020709.html"><div class="icon"><p>その他</p></div>
<div class="txt">2月7日（土曜）の東京競馬開催中止に伴う「指定席・入場券ネット予約」の取扱い</div></a></div></li>
<li class="dento pickup"><div class="news_line"><a href="/news/202602/020809.html"><div class="icon"><p>馬場関連</p></div>
<div class="txt">本日【2月8日（日曜）】の小倉競馬は第4レースを取りやめ、発走時刻をさらに変更します。</div></a></div></li>
<li class="dento pickup"><div class="news_line"><a href="/news/202602/020801.html"><div class="icon"><p>馬場関連</p></div>
<div class="txt">本日【2月8日（日曜）】の東京競馬および京都競馬は開催を中止します</div></a></div></li>
<li class="race"><div class="news_line"><a href="/news/202602/020601.html"><div class="icon"><p>レース関連</p></div>
<div class="txt">東京新聞杯（GⅢ）およびきさらぎ賞（GⅢ）の前日発売を取りやめ</div></a></div></li>
</ul></body></html>"""


def test_parse_cancellations_reads_only_the_official_form():
    got = [(c["race_date"], c["venue"], c["from_race"], c["to_race"]) for c in jra.parse_cancellations(NEWS, 2026)]
    assert got == [("2026-02-07", "東京", 8, None), ("2026-02-08", "小倉", 4, 4),
                   ("2026-02-08", "東京", 1, None), ("2026-02-08", "京都", 1, None)]


def test_cancel_notice_ranges():
    cancels = jra.parse_cancellations(NEWS, 2026)
    assert cancel_notice("2026-02-07|東京|8", cancels) and cancel_notice("2026-02-07|東京|12", cancels)
    assert not cancel_notice("2026-02-07|東京|7", cancels)            # 第7レースまでは行われた
    assert cancel_notice("2026-02-08|小倉|4", cancels) and not cancel_notice("2026-02-08|小倉|5", cancels)
    # 結果の行があれば本当の結果を使う。途中で切れたページも使わない
    race = {"post_time": "13:50"}
    after_post, closed = datetime(2026, 2, 7, 14, 0, tzinfo=JST), datetime(2026, 2, 7, 20, 0, tzinfo=JST)
    assert cancelled_result("2026-02-07|東京|8", cancels, jra.parse_result(empty_result("2026-02-07|東京|8")), race, after_post)
    assert not cancelled_result("2026-02-07|東京|8", cancels, jra.parse_result(empty_result("2026-02-07|東京|8")), race,
                                datetime(2026, 2, 7, 13, 0, tzinfo=JST))          # 発走前は返還にしない
    assert cancelled_result("2026-02-07|東京|8", cancels, None, race, after_post)       # 「以降」の取りやめ：ページが無くても返還
    kokura = "2026-02-08|小倉|4"                                                         # 1レースだけ：お知らせだけでは決めない
    k_mid, k_closed = datetime(2026, 2, 8, 14, 0, tzinfo=JST), datetime(2026, 2, 8, 20, 0, tzinfo=JST)
    assert not cancelled_result(kokura, cancels, None, race, k_closed)
    assert not cancelled_result(kokura, cancels, jra.parse_result(empty_result(kokura)), race, k_mid)
    assert not cancelled_result(kokura, cancels, jra.parse_result("<html><body></body></html>"), race, k_closed)   # 見出しの無い空ページは使わない
    assert not cancelled_result(kokura, cancels, jra.parse_result(empty_result("2026-02-08|小倉|5")), race, k_closed)  # 別のレースのページ
    assert cancelled_result(kokura, cancels, jra.parse_result(empty_result(kokura)), race, k_closed)
    assert not cancelled_result("2026-02-07|東京|8", cancels, jra.parse_result(FULL))
    assert not cancelled_result("2026-02-07|東京|8", cancels, jra.parse_result("<html><body>"))


def test_collector_records_notices_and_stops_refetching(tmp_path, monkeypatch):
    from keiba.live import collector as c
    rk = "2026-02-07|東京|8"
    clock = {"now": datetime(2026, 2, 7, 15, 0, tzinfo=JST)}
    col = Collector(tmp_path, client=None, now=lambda: clock["now"])
    col.state["races"][rk] = {"race_date": "2026-02-07", "venue": "東京", "race_number": 8, "post_time": "13:50",
                              "field_size": 16, "list_cname": "L", "result_links": [("/JRADB/r", "R")]}
    asked = []

    class Client:
        count = 0

        def get(self, path, cname):
            return empty_result(rk)

        def get_url(self, url):
            asked.append(url)
            return NEWS
    col.client = Client()
    monkeypatch.setattr(c.jra, "parse_race_list", lambda page: {"races": []})
    assert col.capture(rk, "result")[-1]["incomplete"]               # お知らせを見る前は待つ
    col.check_notices()
    assert asked == ["https://www.jra.go.jp/news/202602/"]
    rows = load_cancellations(tmp_path / "cancellations.jsonl")
    assert len(rows) == 4 and rows[0]["sha256"]
    col.check_notices()
    assert len(asked) == 1                                           # 30分の間は見に行かない
    col.capture(rk, "result")
    assert f"{rk}|result" in col.state["taken"]                      # 取りやめ：取り直しをやめる
    clock["now"] = datetime(2026, 2, 7, 21, 0, tzinfo=JST)
    assert not [k for k in col.missed() if k.startswith(rk)]          # 「取れなかった」にもしない


def test_notice_failure_does_not_stop_collector(tmp_path):
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 2, 7, 15, 0, tzinfo=JST))
    col.state["races"]["2026-02-07|東京|8"] = {"race_date": "2026-02-07", "venue": "東京", "race_number": 8,
                                               "post_time": "13:50", "field_size": 16}

    class Client:
        count = 0

        def get_url(self, url):
            raise OSError("つながらない")
    col.client = Client()
    logs = []
    col.check_notices(log=logs.append)
    assert logs and "notices_at" not in col.state and not (tmp_path / "cancellations.jsonl").exists()


def _write_cancel(root, race_date, venue, start):
    with open(root / "cancellations.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"race_date": race_date, "venue": venue, "from_race": start, "to_race": None,
                            "title": f"本日の{venue}競馬は第{start}レース以降を取りやめ"}, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("page", [None, empty_result(RK)])
def test_wallet_refunds_cancelled_race(tmp_path, page):
    from keiba.live.wallet import run_live
    orig, (paths, col) = _wallet_setup(tmp_path)
    try:
        run_live(paths)
        if page:
            col._save(RK, "result", "result", "/JRADB/x", "c", page)
        assert run_live(paths)["settled"] == 0
        _write_cancel(tmp_path / "live", "2025-08-30", "東京", 8)
        assert run_live(paths)["settled"] == 3 * 5
        rows = _settlements(paths)
        assert all(s["status"] in ("refunded_void", "not_allocated_gate", "no_bet", "not_allocated") or s["stake"] == 0
                   for s in rows)
        assert all(s["returned"] == s["stake"] for s in rows) and any(s["stake"] > 0 for s in rows)
        assert run_live(paths)["settled"] == 0                     # 二度書かない
    finally:
        jra.parse_card = orig


def test_wallet_uses_real_result_even_if_notice_says_cancelled(tmp_path):
    from keiba.live.wallet import run_live
    orig, (paths, col) = _wallet_setup(tmp_path)
    try:
        run_live(paths)
        _write_cancel(tmp_path / "live", "2025-08-30", "東京", 1)
        col._save(RK, "result", "result", "/JRADB/x", "c", FULL)
        run_live(paths)
        assert not [s for s in _settlements(paths) if s["status"] == "refunded_void"]
    finally:
        jra.parse_card = orig


def _app_paths(tmp_path, result_page=None):
    from keiba.config import Paths
    paths = Paths(data_dir=tmp_path / "data")
    live = paths.data_dir / "live"
    (live / "snapshots").mkdir(parents=True)
    rk = "2026-02-07|東京|8"
    (live / "state.json").write_text(json.dumps({"races": {rk: {"race_date": "2026-02-07", "venue": "東京", "race_number": 8,
                                                                "post_time": "13:50", "field_size": 16,
                                                                "race_name": "4歳以上1勝クラス"}}}, ensure_ascii=False))
    (live / "snapshots.jsonl").write_text("")
    if result_page is not None:
        (live / "snapshots" / "r.html").write_text(result_page)
        (live / "snapshots.jsonl").write_text(json.dumps({"race_key": rk, "kind": "result", "time_point": "result",
                                                          "path": "snapshots/r.html", "fetched_at": "2026-02-07T15:00:00+09:00"},
                                                         ensure_ascii=False) + "\n")
    return paths, live, rk


@pytest.mark.parametrize("page", [None, empty_result("2026-02-07|東京|8")])
def test_app_refunds_purchase_on_cancelled_race(tmp_path, page):
    from keiba.app.data import Source
    from keiba.app.pages import money_text
    from keiba.app.store import settle_purchase
    paths, live, rk = _app_paths(tmp_path, page)
    entry = {"kind": "buy", "total": 100, "stakes": {"1-2-3": 100}}
    assert settle_purchase(entry, Source(paths, "live").results.get(rk))["status"] == "pending"
    _write_cancel(live, "2026-02-07", "東京", 8)
    res = Source(paths, "live").results[rk]
    st = settle_purchase(entry, res)
    assert res["cancelled"] and st["status"] == "refunded_void" and st["returned"] == 100 and st["counted"]
    assert "取りやめ" in money_text(st)


def test_app_keeps_truncated_page_waiting_even_with_notice(tmp_path):
    from keiba.app.data import Source
    paths, live, rk = _app_paths(tmp_path, "<html><body>")
    _write_cancel(live, "2026-02-07", "東京", 8)
    assert rk not in Source(paths, "live").results


def test_bad_cancel_lines_are_ignored(tmp_path):
    f = tmp_path / "cancellations.jsonl"
    f.write_text('{"race_date": "2026-02-07", "venue": "東京", "from_race": "8"}\n'
                 '{"race_date": "2026-02-07", "venue": "東京", "from_race": true}\n'
                 '{"race_date": "2026-02-07", "venue": "東京", "from_race": 8, "to_race": null}\n{"race_d', encoding="utf-8")
    assert len(load_cancellations(f)) == 1


# ---------------- 3. 比較を複数のタブで開いたとき ----------------

def test_compare_keeps_several_pairs(tmp_path, monkeypatch):
    import os
    import sqlite3 as sq
    from keiba.app import data as d
    from keiba.app.server import App
    from keiba.config import Paths
    paths = Paths(data_dir=tmp_path / "data")
    paths.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    paths.ledger_path.write_bytes(b"")
    app = App(paths, store_path=tmp_path / "p.sqlite")
    locked = {"on": False}

    def fake(lp, va, vb, *a, **k):
        if locked["on"]:
            raise sq.OperationalError("database is locked")
        return {"races": 1, "rows": [], "a": va, "b": vb, "tp": "1h", "wallet": "ref_top10_equal"}
    monkeypatch.setattr(d, "version_compare", fake)
    app.compare("A", "B")
    app.compare("B", "C")
    locked["on"] = True
    os.utime(paths.ledger_path, (1, 1))
    assert app.compare("A", "B")["stale"] and app.compare("B", "C")["stale"]


# ---------------- 4. クラス ----------------

def test_race_class_from_name():
    from keiba.app.data import race_class_of
    assert race_class_of("2歳未勝利牝[指定]") == "未勝利"
    assert race_class_of("メイクデビュー京都 2歳新馬(混合)[指定]") == "新馬"
    assert race_class_of("秋明菊賞 2歳1勝クラス(混合)(特指)") == "1勝クラス"
    assert race_class_of("3歳以上2勝クラス(混合)[指定]") == "2勝クラス"
    assert race_class_of("マイルチャンピオンS 3歳以上オープン(国際)(指定)") == "オープン"
    assert race_class_of("") is None and race_class_of(None) is None


def test_class_is_kept_for_live_and_archived_races(tmp_path):
    from datetime import date, timedelta
    from keiba.app.data import Source
    paths, live, rk = _app_paths(tmp_path)
    assert Source(paths, "live").races[rk]["race_class"] == "1勝クラス"
    old = (date.today() - timedelta(days=10)).isoformat()
    ork = f"{old}|福島|1"
    (live / "races_archive.jsonl").write_text(json.dumps({"race_key": ork, "race_name": "2歳未勝利", "field_size": 8,
                                                          "post_time": "10:00"}, ensure_ascii=False) + "\n")
    with open(live / "snapshots.jsonl", "a") as f:
        f.write(json.dumps({"race_key": ork, "kind": "card", "time_point": "24h", "path": "snapshots/none.html",
                            "fetched_at": f"{old}T09:00:00+09:00"}, ensure_ascii=False) + "\n")
    assert Source(paths, "live").races[ork]["race_class"] == "未勝利"


# ---------------- 5. 別の欄の「取消」の文字 ----------------

def test_scratch_mark_only_from_number_or_odds_cell():
    ids = [f"H{h:04d}" for h in range(8)]
    note = card_html(ids).replace('<td class="num">3</td>', '<td class="num">3</td><td class="rating_notes">取消前の成績</td>')
    assert not [c for c in jra.parse_card(note) if c["scratch"]]
    real = card_html(ids).replace('<td class="num">3</td>', '<td class="num"><span class="cap">取消</span></td>')
    assert [c["scratch"] for c in jra.parse_card(real)].count("取消") == 1


# ---------------- 2の追加：実物で見つけたほかの見出しの形 ----------------

def _news(*titles):
    items = "".join(f'<li><div class="news_line"><a href="/news/x.html"><div class="txt">{t}</div></a></div></li>' for t in titles)
    return f"<html><body><ul>{items}</ul></body></html>"


def test_other_real_title_forms():
    page = _news("本日【5月31日(土曜)】の東京競馬第9レースの取りやめおよび第10レース以降の発走時刻変更",
                 "明日【9月21日(祝日・月曜)】の中山競馬は開催を中止します",
                 "第2回中京競馬第7日第2競走の取りやめ",
                 "京都競馬とWIN5の土曜日夜間発売を取りやめ",
                 "9月21日(祝日・月曜)の中山競馬開催中止に伴う「指定席・入場券ネット予約」の取扱い")
    got = [(c["race_date"], c["venue"], c.get("kai"), c.get("nichi"), c["from_race"], c["to_race"])
           for c in jra.parse_cancellations(page, 2026, 9)]
    assert got == [("2026-05-31", "東京", None, None, 9, 9), ("2026-09-21", "中山", None, None, 1, None),
                   (None, "中京", 2, 7, 2, 2)]


def test_january_notice_in_december_list_is_next_year():
    page = _news("明日【1月5日(月曜)】の中山競馬は開催を中止します")
    assert jra.parse_cancellations(page, 2026, 12)[0]["race_date"] == "2027-01-05"
    assert jra.parse_cancellations(page, 2027, 1)[0]["race_date"] == "2027-01-05"


def test_kai_form_is_dated_from_schedule_and_previous_month_is_checked(tmp_path):
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 8, 1, 10, 0, tzinfo=JST))
    col.state["races"]["2026-08-01|中京|2"] = {"race_date": "2026-08-01", "venue": "中京", "race_number": 2,
                                              "post_time": "10:20", "kai": 2, "nichi": 7}
    asked = []

    class Client:
        count = 0

        def get_url(self, url):
            asked.append(url)
            return _news("第2回中京競馬第7日第2競走の取りやめ") if url.endswith("/202608/") else _news()
    col.client = Client()
    col.check_notices()
    assert asked == ["https://www.jra.go.jp/news/202607/", "https://www.jra.go.jp/news/202608/"]   # 前日（7月31日）の月も見る
    rows = load_cancellations(tmp_path / "cancellations.jsonl")
    assert [(r["race_date"], r["venue"], r["from_race"], r["to_race"]) for r in rows] == [("2026-08-01", "中京", 2, 2)]
    assert cancel_notice("2026-08-01|中京|2", rows) and not cancel_notice("2026-08-01|中京|3", rows)


def test_kai_form_without_matching_race_is_retried_later(tmp_path):
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 8, 1, 10, 0, tzinfo=JST))
    col.state["races"]["2026-08-01|中京|2"] = {"race_date": "2026-08-01", "venue": "中京", "race_number": 2, "post_time": "10:20"}

    class Client:
        count = 0

        def get_url(self, url):
            return _news("第2回中京競馬第7日第2競走の取りやめ")
    col.client = Client()
    col.check_notices()
    assert not load_cancellations(tmp_path / "cancellations.jsonl")      # 回次・日次が分からなければ書かない（推測しない）
