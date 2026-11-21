"""JRA公式からの収集（ネットにはつながず、合成した小さなHTMLで試す）。"""
import re
import json
from datetime import datetime

from keiba.live import jra
from keiba.live.collector import JST, Collector

INDEX = "<a href='#' onclick=\"return doAction('/JRADB/accessD.html', 'pw01drl00082026050620261122/28');\">京都</a>"


def race_list(odds=False, result=False):
    odds_cell = ("<a href='#' onclick=\"return doAction('/JRADB/accessO.html', 'pw15orl10/AA');\">オッズ</a>" if odds else "未発表")
    res_cell = ("<a href='/JRADB/accessS.html?CNAME=pw01sde1008202605061120261122/BD'>結果</a>" if result else "未確定")
    return f"""<table id="race_list"><caption><h2>2026年11月22日（日曜） 5回京都6日</h2></caption><tbody><tr>
<th class="race_num"><a href="/JRADB/accessD.html?CNAME=pw01dde0108202605061120261122/55"><img alt="11レース"></a></th>
<td class="time">15時40分</td><td class="race_name">テストS</td>
<td class="dist"><span class="course">芝</span><span class="dist">1,600m</span><span class="num">18頭</span></td>
<td class="syutsuba"><a href="/JRADB/accessD.html?CNAME=pw01dde0108202605061120261122/55">出馬表</a></td>
<td class="odds">{odds_cell}</td><td class="result">{res_cell}</td></tr></tbody></table>"""


CARD = """<table><tbody><tr><td class="waku"><img alt="枠3"></td><td class="num">5</td><td class="horse">
<div class="name"><a href="/JRADB/accessU.html?CNAME=pw01dud002022105396/56">テストウマ</a></div>
<p class="trainer"><a href="#">調教 師</a></p><ul><li class="sire"><span>父：</span>チチ</li>
<li class="mare"><span>母：</span>ハハ<span class="bloodmare">(母の父：ハハチチ)</span></li></ul></td>
<td class="jockey"><p class="age">牝4/鹿</p><p class="weight">55.0<span>kg</span></p><p class="jockey"><a href="#">騎 手</a></p></td>
<td class="past p1"><div class="date">2026年10月17日</div><div class="rc">東京</div><div class="place">6<span>着</span></div>
<span class="dist">1600芝</span><p class="time">1:32.8</p><div class="f3">3F 35.2</div></td></tr></tbody></table>"""

ODDS = """<a href='#' onclick="return doAction('/JRADB/accessO.html', 'pw151ou1/11');">単勝・複勝</a>
<a href='#' onclick="return doAction('/JRADB/accessO.html', 'pw156ou1/66');">3連複</a>"""


class FakeClient:
    def __init__(self, pages):
        self.pages, self.count, self.calls = pages, 0, []

    def get(self, path, cname):
        self.count += 1
        self.calls.append(cname)
        return self.pages(cname)


def test_parse_race_list_and_card():
    m = jra.parse_race_list(race_list(odds=True))
    assert m["race_date"] == "2026-11-22" and m["venue"] == "京都"
    r = m["races"][0]
    assert r["race_number"] == 11 and r["post_time"] == "15:40" and r["distance_m"] == 1600 and r["field_size"] == 18
    assert r["card_cname"] == "pw01dde0108202605061120261122/55" and r["odds_links"] == [("/JRADB/accessO.html", "pw15orl10/AA")]
    c = jra.parse_card(CARD)[0]
    assert c["horse_number"] == 5 and c["waku"] == 3 and c["horse_id"] == "2022105396"
    assert (c["sex"], c["age"], c["weight_carried"]) == ("牝", 4, 55.0)
    assert (c["sire"], c["dam"], c["damsire"]) == ("チチ", "ハハ", "ハハチチ")
    assert c["past_runs"][0]["finish"] == 6 and c["past_runs"][0]["f3"] == "35.2"


def _collector(tmp_path, when, odds=True, result=False):
    def pages(cname):
        if cname.startswith("pw01dli"):
            return INDEX
        if cname.startswith("pw01drl"):
            return race_list(odds=odds, result=result)
        if cname.startswith("pw01dde"):
            return CARD
        if cname.startswith("pw15orl"):
            return ODDS
        return "<html>odds or result</html>"
    clock = {"now": when}
    col = Collector(tmp_path, client=FakeClient(pages), now=lambda: clock["now"])
    return col, clock


def test_time_point_windows_and_no_double_capture(tmp_path):
    col, clock = _collector(tmp_path, datetime(2026, 11, 22, 14, 30, tzinfo=JST))  # 発走70分前 → 1h の窓（締切は60分前）
    out = col.tick()
    assert out["saved"] >= 3
    kinds = [json.loads(x)["kind"] for x in (tmp_path / "snapshots.jsonl").read_text().splitlines()]
    assert kinds[:4] == ["card", "odds_first", "odds_win", "odds_trio"]
    rec = json.loads((tmp_path / "snapshots.jsonl").read_text().splitlines()[0])
    assert rec["time_point"] == "1h" and rec["race_key"] == "2026-11-22|京都|11" and len(rec["sha256"]) == 64
    assert (tmp_path / rec["path"]).exists()
    assert col.tick()["saved"] == 0  # 同じ窓では二度取らない
    clock["now"] = datetime(2026, 11, 22, 15, 15, tzinfo=JST)  # 25分前 → 15m の窓（締切は15分前）
    assert col.tick()["saved"] >= 2


def test_odds_not_yet_on_sale_is_recorded(tmp_path):
    col, _ = _collector(tmp_path, datetime(2026, 11, 21, 15, 30, tzinfo=JST), odds=False)  # 前日（24h の窓。締切は24時間前）
    col.tick()
    kinds = [json.loads(x)["kind"] for x in (tmp_path / "snapshots.jsonl").read_text().splitlines()]
    assert kinds == ["card", "odds_unavailable"]


def test_result_waits_until_link_appears(tmp_path):
    col, clock = _collector(tmp_path, datetime(2026, 11, 22, 16, 30, tzinfo=JST), result=False)
    col.tick()
    assert "2026-11-22|京都|11|result" not in col.state["taken"]
    from tests.test_live_wallet import result_html
    full = result_html([3, 1, 2] + list(range(4, 19)))          # 18頭すべての着順と三連複の払戻がそろった結果
    col.client.pages = lambda c: race_list(result=True) if c.startswith("pw01drl") else "<html>result</html>"
    clock["now"] = datetime(2026, 11, 22, 16, 40, tzinfo=JST)
    col.tick()
    assert "2026-11-22|京都|11|result" not in col.state["taken"]   # 中身のない結果ページは取得済みにしない
    col.client.pages = lambda c: race_list(result=True) if c.startswith("pw01drl") else full
    clock["now"] = datetime(2026, 11, 22, 16, 45, tzinfo=JST)
    col.tick()
    assert "2026-11-22|京都|11|result" in col.state["taken"]


def test_client_spacing_and_cap():
    calls = []

    class Res:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        calls.append(req.full_url)
        return Res("テスト".encode("cp932"))

    c = jra.Client(max_requests=2, interval=0.0, opener=opener)
    assert c.get("/JRADB/accessD.html", "x") == "テスト"
    c.get("/JRADB/accessD.html", "y")
    try:
        c.get("/JRADB/accessD.html", "z")
        assert False
    except RuntimeError:
        pass
    assert calls == ["https://www.jra.go.jp/JRADB/accessD.html"] * 2


def test_no_capture_after_deadline(tmp_path):
    col, _ = _collector(tmp_path, datetime(2026, 11, 22, 15, 32, tzinfo=JST))  # 発走8分前：15m の締切を過ぎている
    assert col.tick()["saved"] == 0


def test_pruned_races_are_archived(tmp_path):
    """予定表から外す（7日より前）ときに、頭数・発走時刻を races_archive.jsonl に追記して残す。"""
    from keiba.live.collector import Collector
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 12, 1, 10, 0, tzinfo=JST))
    col.state["races"]["2026-11-21|福島|1"] = {"race_date": "2026-11-21", "venue": "福島", "race_number": 1,
                                              "race_name": "2歳未勝利", "post_time": "09:45", "field_size": 15}
    col.state["races"]["2026-11-30|福島|1"] = {"race_date": "2026-11-30", "venue": "福島", "race_number": 1,
                                              "post_time": "09:45", "field_size": 15}
    col.refresh = lambda *a, **k: None
    col.tick(log=lambda *a: None)
    lines = [json.loads(x) for x in (tmp_path / "races_archive.jsonl").read_text().splitlines()]
    assert [x["race_key"] for x in lines] == ["2026-11-21|福島|1"]
    assert lines[0]["field_size"] == 15 and lines[0]["post_time"] == "09:45" and lines[0]["race_name"] == "2歳未勝利"
    assert "2026-11-21|福島|1" not in col.state["races"] and "2026-11-30|福島|1" in col.state["races"]


def test_interrupted_archive_append_is_repaired_and_verified(tmp_path):
    from keiba.live.collector import ARCHIVE_FILE, archived_keys
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 12, 1, 10, 0, tzinfo=JST))
    for n in (1, 2):
        col.state["races"][f"2026-11-21|福島|{n}"] = {"race_date": "2026-11-21", "venue": "福島", "race_number": n,
                                                     "post_time": "09:45", "field_size": 15}
    (tmp_path / ARCHIVE_FILE).write_text('{"race_key": "2026-11-14|東京|1", "field_si')   # 前回の追記が途中で止まった
    col.refresh = lambda *a, **k: None
    col.tick(log=lambda *a: None)
    assert archived_keys(tmp_path / ARCHIVE_FILE) == {"2026-11-21|福島|1", "2026-11-21|福島|2"}
    assert not col.state["races"]


def test_failed_archive_write_keeps_races_scheduled(tmp_path, monkeypatch):
    from keiba.live import collector as c
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 12, 1, 10, 0, tzinfo=JST))
    col.state["races"]["2026-11-21|福島|1"] = {"race_date": "2026-11-21", "venue": "福島", "race_number": 1,
                                              "post_time": "09:45", "field_size": 15}
    col.refresh = lambda *a, **k: None

    def broken(path, lines):
        raise OSError("disk full")
    monkeypatch.setattr(c, "append_lines", broken)
    col.tick(log=lambda *a: None)
    assert "2026-11-21|福島|1" in col.state["races"]                  # 記録できなければ予定表に残す


def test_refresh_keeps_known_head_count(tmp_path, monkeypatch):
    from keiba.live import collector as c
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 11, 22, 9, 0, tzinfo=JST))
    rk = "2026-11-22|東京|1"
    col.state["races"][rk] = {"race_date": "2026-11-22", "venue": "東京", "race_number": 1, "post_time": "10:00",
                              "field_size": 4, "known_at": "2026-11-21T09:00:00+09:00"}
    meeting = {"race_date": "2026-11-22", "venue": "東京",
               "races": [{"race_number": 1, "post_time": "10:00", "field_size": None, "card_cname": "new"}]}   # 頭数の欄だけ読めない
    monkeypatch.setattr(c.jra, "links", lambda page: [("/JRADB/accessD.html", "pw01drl-test")])
    monkeypatch.setattr(c.jra, "parse_race_list", lambda page: meeting)

    class Client:
        count = 0

        def get(self, path, cname):
            return ""
    col.client = Client()
    col.refresh(force=True)
    race = col.state["races"][rk]
    assert race["field_size"] == 4 and race["card_cname"] == "new"      # 確かめた頭数は残し、新しい値は取り込む


def test_partial_result_is_fetched_again(tmp_path, monkeypatch):
    """途中までの結果（着順の空欄・払戻の欠け）は「取得済み」にせず、窓の間は次の回に取り直す。"""
    from keiba.live import collector as c
    from tests.test_live_wallet import result_html
    rk = "2026-11-22|東京|1"
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026, 11, 22, 11, 0, tzinfo=JST))
    col.state["races"][rk] = {"race_date": "2026-11-22", "venue": "東京", "race_number": 1, "post_time": "10:00",
                              "field_size": 4, "list_cname": "L", "result_links": [("/JRADB/r", "R")]}
    partial = result_html([1, 2, 3, 4]).replace('<td class="place">4</td>', '<td class="place"></td>')
    pages = {"R": partial}

    class Client:
        count = 0

        def get(self, path, cname):
            return pages.get(cname, "")
    col.client = Client()
    monkeypatch.setattr(c.jra, "parse_race_list", lambda page: {"races": []})
    saved = col.capture(rk, "result")
    assert saved and saved[-1]["incomplete"] and f"{rk}|result" not in col.state["taken"]
    pages["R"] = result_html([1, 2, 3, 4])
    col.capture(rk, "result")
    assert f"{rk}|result" in col.state["taken"]


def test_result_issue_rules():
    from keiba.live.collector import result_issue
    from tests.test_live_wallet import result_html
    assert result_issue(result_html([3, 1, 2, 4]), 4) is None
    assert result_issue(re.sub(r'<li class=\"trio\">.*?</li>', '', result_html([3, 1, 2, 4])), 4)          # 払戻がまだ無い
    assert result_issue(result_html([3, 1, 2, 4]), None)                                     # 頭数が分からない
    assert result_issue(result_html([3, 1]), 4)                                              # 行が欠けている
