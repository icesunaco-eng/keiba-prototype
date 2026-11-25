"""💚20261125-10（みにの受け入れ再検証）の指摘。どれも本番と同じ入口（cli の collect tick）や、
見回りと画面が途中で重なる順番から通す。"""
import json
import sqlite3
import types
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from keiba import cli, official
from keiba.app.checks import material_timing
from keiba.app.data import Cache, Source
from keiba.app.pages import wallet_table
from keiba.config import Paths
from keiba.ledger import Ledger
from keiba.live import collector as collector_module, mam, wallet
from keiba.live.collector import Collector, JST, deadline
from keiba.live.jra import ENTRY_INDEX
from keiba.review import build_review
from tests.test_cancel_and_truncation import empty_result
from tests.test_capture_window_shift import CARD, DAY, LIST, RK, T, _pages, _paths
from tests.test_live_collector import INDEX, FakeClient, race_list
from tests.test_live_wallet import card_html, result_html, trio_html, win_html


# ---- 止める①：一覧の出馬表リンクだけ欠け、新しい時間帯でオッズだけ取れた ----------------------------------

def _replay_card_link_outage(directory, outage):
    """14:25に15:40の時間帯で取ったあと、発走が15:45に変わる。outage なら 14:30〜16:16 の一覧で出馬表リンクだけ欠ける。"""
    paths = _paths(directory)
    clock = {"now": T("14:00")}
    root = paths.data_dir / "live"

    def fetch(cname):
        if cname == ENTRY_INDEX[1]:
            return INDEX
        pages = _pages(clock)
        if cname == LIST:
            listing = race_list(odds=True, result=clock["now"] >= T("16:16")).replace(
                "15時40分", "15時40分" if clock["now"] < T("14:30") else "15時45分").replace("18頭", "8頭")
            if outage and T("14:30") <= clock["now"] < T("16:16"):
                listing = listing.replace(f"?CNAME={CARD}", "")   # 出馬表リンクだけ欠ける（オッズ欄・頭数は残る）
            return listing
        if T("14:30") <= clock["now"] < T("14:45"):
            if cname == "pw151ou1/11":
                return win_html(time="14時30分")
            if cname == "pw156ou1/66":
                return trio_html(time="14時30分")
        if cname == "pw01sde1008202605061120261122/BD":
            return result_html([1, 2, 4, 5, 6, 7, 8, 3]).replace(
                '<td class="place">8</td><td class="num">3</td>', '<td class="place">取消</td><td class="num">3</td>')
        return pages[cname]

    client = FakeClient(fetch)
    real_connect = sqlite3.connect

    def tick(hm, locked=False):
        clock["now"] = T(hm)
        out = StringIO()
        quick = (lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.2})) if locked else real_connect
        with patch.object(sqlite3, "connect", quick), \
                patch.object(collector_module, "Collector",
                             lambda folder: Collector(folder, client=client, now=lambda: clock["now"])), \
                patch.object(Collector, "check_notices", lambda self, log=print: None), \
                patch.object(cli, "_notify", lambda *a: None), \
                patch.object(wallet, "prediction_clock", lambda: clock["now"]), \
                patch.object(Ledger, "_now", lambda _: clock["now"].astimezone(timezone.utc).isoformat(timespec="seconds")), \
                redirect_stdout(out):
            cli.cmd_collect(paths, SimpleNamespace(action="tick"))
        return json.loads(out.getvalue().splitlines()[-1]), Collector(root, client=client, now=lambda: clock["now"])

    tick("14:00")
    writer = real_connect(paths.ledger_path)
    try:
        writer.execute("BEGIN EXCLUSIVE")   # 14:25の予想は台帳の鍵で書けず、取得だけ残る
        tick("14:25", locked=True)
    finally:
        writer.rollback()
        writer.close()
    for hm in ("14:30", "14:35", "14:40", "14:46"):
        _, col = tick(hm)
    assert col.state["races"][RK]["post_time"] == "15:45"
    return paths, root, col, tick


def test_card_link_outage_does_not_bet_with_card_from_before_the_window(tmp_path):
    """💚20261125-10 止める①：新しい時間帯の出馬表が取れず、オッズだけ取れた。時間帯より前の出馬表（取消を
    知らない）で両券種を合格にせず、取消馬入りの組にお金を配分しない。時点は「取れなかった」と知らせる。"""
    paths, root, col, tick = _replay_card_link_outage(tmp_path / "outage", True)
    assert f"{RK}|1h" in col.missed()                                  # オッズだけでは取得済みにしない
    _, _, inputs = wallet.live_tables(root, col.state, wallet._snapshots(root))
    inp = inputs[(RK, "1h")]
    assert not inp["captured"] and not inp["win"]["ok"] and not inp["gate"]["pass"]
    assert any("出馬表が取得の時間帯より前" in r for r in inp["gate"]["reasons"]), inp["gate"]["reasons"]
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    assert preds                                                        # 締切の代わりの予想は作る
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes"), (p["method"], a, v)       # でもお金は配分しない
    src = Source(paths, "live")
    shown = [(m, tp) for (m, tp) in src.preds[RK] if tp == "1h"]
    assert shown and all(material_timing(src.preds[RK][k], src.races[RK], "1h")[0] == "fallback" for k in shown)
    tick("16:16")
    w = wallet.wallet_summary(paths)
    assert not len(w) or int(w["stake"].sum()) == 0                      # 返還で帳尻を合わせる配分もない


def test_card_link_normal_recapture_is_unchanged(tmp_path):
    """対照：新しい時間帯で出馬表もオッズも取れた（取消を読む）場合は、これまでどおり時点の材料として使う。"""
    paths, root, col, _ = _replay_card_link_outage(tmp_path / "normal", False)
    assert f"{RK}|1h" not in col.missed()
    _, _, inputs = wallet.live_tables(root, col.state, wallet._snapshots(root))
    inp = inputs[(RK, "1h")]
    assert inp["captured"]
    assert not any("出馬表が取得の時間帯より前" in r for r in inp["gate"]["reasons"])
    src = Source(paths, "live")
    assert material_timing(src.preds[RK][("ml_lgbm", "1h")], src.races[RK], "1h")[0] == "ok"


# ---- 直してから②：発走が早まり、前の取得が新しい締切より後になった ------------------------------------------

@pytest.mark.parametrize("first, missed", [("14:34", False), ("14:39", True)])
def test_post_moved_earlier_after_capture_is_reported_missed(tmp_path, first, missed):
    """💚20261125-10 直してから②：15:40の時間帯で取ったあと、14:44の一覧で発走が15:35（1時間前の締切14:35）に
    繰り上がった。14:39の取得は新しい締切より後なので「取れなかった」に数えて知らせる（14:34は時間帯の中）。"""
    clock = {"now": T(first)}

    def page(name):
        if name.startswith("pw01dli"):
            return INDEX
        pages = _pages(clock)
        return (pages[name].replace("15時41分", "15時35分" if clock["now"] >= T("14:44") else "15時40分")
                if name == LIST else pages[name])
    root = tmp_path / "live"
    col = Collector(root, client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "known_at": T("09:00").isoformat()}
    col.state["refreshed_at"] = T("14:14").isoformat()   # 次のふつうの一覧の更新は14:44
    col.record_post_times()
    col._write_state()
    told = []
    with patch.object(Collector, "check_notices", lambda *_: None):
        assert col.tick(log=lambda *_: None)["saved"] == 4
        clock["now"] = T("14:44")
        col.tick(log=lambda *_: None, notify=lambda title, message: told.append(message))
    assert col.state["races"][RK]["post_time"] == "15:35"
    key = f"{RK}|1h"
    got = official.material_time(official.capture_records(root)[(RK, "1h")], T("14:35"))
    assert (got is None) == missed
    assert (key in json.loads((root / "health.json").read_text())["missed"]) == missed
    assert any(key in m for m in told) == missed


# ---- 止める②：発走時刻の記録の穴埋めと、画面の読み込みが重なる ---------------------------------------------

def test_history_completion_during_app_read_keeps_the_result(tmp_path):
    """💚20261125-10 止める②：画面が途中の記録を読んだ直後に、別の見回りが穴埋めを終えて印を立てた。
    同じ読み込みの中で途中の記録と完了の印を混ぜず、200円・9,120円の行を落とさない。"""
    from keiba.live import collector
    rk = f"{DAY}|東京|3"
    post = datetime.fromisoformat(f"{DAY}T15:40:00+09:00")
    paths = Paths(data_dir=tmp_path)
    root = tmp_path / "live"
    now = post + timedelta(days=2)
    client = SimpleNamespace(count=0)
    col = Collector(root, client=client, now=lambda: now)
    col.state["races"][rk] = {"race_date": DAY, "venue": "東京", "race_number": 3, "post_time": "15:41",
                             "field_size": 8, "surface": "芝", "distance_m": 1600}

    def save(at, kind, tp, html):
        col.now = lambda: at
        col._save(rk, kind, tp, "/x", "synth", html)

    def card_at(label):
        return f"<html><body><p>発走時刻：<strong>{label}</strong></p>{card_html(range(8))}</body></html>"
    save(post - timedelta(days=1, minutes=10), "card", "24h", card_at("15時40分"))
    save(post - timedelta(minutes=70), "card", "1h", card_at("15時41分"))
    save(post - timedelta(minutes=69), "odds_trio", "1h", trio_html(time="14時30分"))
    save(post + timedelta(minutes=31), "result", "result", result_html(list(range(1, 9))))
    col._write_state()
    col.now = lambda: now
    led = Ledger(paths.ledger_path)
    v, m = "live-well-formed-old", "ml_lgbm"
    led._now = lambda: (post - timedelta(minutes=49)).astimezone(timezone.utc).isoformat(timespec="seconds")
    led.add_prediction(rk, "1h", m, v, {"race_date": DAY, "period": "live", "horses": [],
                                        "trios": [{"combo": "1-2-3", "p": .2}],
                                        "allocation": {a: {"status": "allocated", "stakes": {"1-2-3": 200}, "total": 200}
                                                       for a in wallet.ALLOC_METHODS}}, "i", "m", "c")
    for a in wallet.ALLOC_METHODS:
        led.add_settlement(rk, "1h", m, v, a, {"status": "settled", "stake": 200, "returned": 9120, "hits": ["1-2-3"]})
    led.commit()

    def shown():
        return {(int(x["stake"]), int(x["returned"])) for x in wallet_table(Cache(paths).get("live"), "ev_weighted")}
    assert shown() == {(200, 9120)}

    class Cut(BaseException):
        pass

    def cut_append(path, lines):   # 初めての穴埋めの追記で、1行目を書いた後・2行目の途中で止まる
        path.write_text(lines[0] + "\n" + lines[1][:18])
        raise Cut()
    with patch.object(collector, "append_lines", cut_append), pytest.raises(Cut):
        col.record_post_times()
    resumed = Collector(root, client=client, now=lambda: now)
    assert "post_history_filled" not in resumed.state
    real_read, done = official.recorded_posts, []

    def read_then_finish(r):
        records = real_read(r)
        if not done:   # 画面が途中の記録を読んだ直後に、見回りが穴埋めを終えて印を立てる
            done.append(1)
            resumed.record_post_times()
            resumed._write_state()
        return records
    with patch.object(official, "recorded_posts", read_then_finish):
        during = shown()
    assert done and during == {(200, 9120)}
    assert shown() == {(200, 9120)}
    assert {(int(r.stake), int(r.returned)) for r in wallet.wallet_summary(paths).itertuples()} == {(200, 9120)}
    assert {(int(r["stake"]), int(r["returned"])) for r in build_review(paths, DAY[:7])["wallets"]} == {(200, 9120)}
    led.close()


# ---- 止める③：8日止まったあとのふつうの見回り（cli）で、予定表から外れたレースを精算する ---------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    from keiba.live import jra
    from tests.test_live_wallet import RK as WRK, _setup
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card", lambda p: [dict(x, horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    paths, col = _setup(tmp_path)
    wallet.run_live(paths)
    return paths, col, WRK


_REAL_RUN_LIVE = wallet.run_live   # 差し替える前の本物（回ごとに時計だけ変える）
from keiba import review as _review_module  # noqa: E402
_REAL_WRITE_REVIEW = _review_module.write_review


def _cli_tick(paths, monkeypatch, now, pages=None, months=None):
    class Cl:
        count = 0
        def get(self, path, cname): return (pages or {}).get(cname, "<html></html>")
        def get_url(self, url): return "<html></html>"
    real = collector_module.Collector
    monkeypatch.setattr(collector_module, "Collector", lambda root: real(root, client=Cl(), now=lambda: now))
    monkeypatch.setattr(real, "refresh", lambda self, force=False: None)
    monkeypatch.setattr(real, "check_notices", lambda self, log=print: None)
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(mam, "export_packets", lambda p: [])
    monkeypatch.setattr(wallet, "run_live", lambda p, **k: _REAL_RUN_LIVE(p, now=now))
    import keiba.review as review
    monkeypatch.setattr(review, "write_review", lambda p, month, *a: (months if months is not None else []).append(month))
    out = StringIO()
    with redirect_stdout(out):
        cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
    monkeypatch.setattr(collector_module, "Collector", real)
    return json.loads(out.getvalue().splitlines()[-1])


def _rows(paths, rk):
    led = Ledger(paths.ledger_path, read_only=True)
    out = [s for s in led.settlements() if s["race_key"] == rk]
    led.close()
    return out


def _cancel(root, rk):
    day, venue, n = rk.split("|")
    with open(root / "cancellations.jsonl", "a") as f:
        f.write(json.dumps(dict(race_date=day, venue=venue, from_race=int(n), to_race=int(n), title="取りやめ",
                                found_at=f"{day}T16:00:00+09:00"), ensure_ascii=False) + "\n")


def test_cancel_settles_through_ordinary_tick_after_long_stop(env, monkeypatch):
    """💚20261125-10 止める③：1レースの取りやめで空の結果を保存した回は返還の前に終わり、Macが8日止まった。
    再開のふつうの見回りで予定表から外れても、その回に仮想財布の返還を書き、レースの月の月次も作り直す。"""
    paths, col, rk = env
    _cancel(col.root, rk)
    col.state["races"][rk].update(list_cname="L", result_links=[["/x", "R"]], known_at="2025-08-30T09:00:00+09:00")
    col._write_state()
    first = _cli_tick(paths, monkeypatch, datetime(2025, 8, 30, 16, 10, tzinfo=JST), {"R": empty_result(rk)})
    saved = [json.loads(x) for x in (col.root / "snapshots.jsonl").read_text().splitlines()]
    assert [s for s in saved if s["race_key"] == rk and s["kind"] == "result"]   # 空の結果は保存した
    assert not _rows(paths, rk) and first.get("live", {}).get("settled", 0) == 0   # 窓が閉じる前は待つ
    assert not json.loads((col.root / "state.json").read_text()).get("live_pending")
    months = []
    later = _cli_tick(paths, monkeypatch, datetime(2025, 9, 7, 16, 0, tzinfo=JST), months=months)
    assert rk not in json.loads((col.root / "state.json").read_text())["races"]
    got = _rows(paths, rk)
    assert later["live"]["settled"] == len(got) > 0
    assert [s for s in got if s["status"] == "refunded_void" and s["returned"] == s["stake"] > 0]
    assert "2025-08" in months                                          # 外したレースの月の月次
    again = _cli_tick(paths, monkeypatch, datetime(2025, 9, 7, 16, 5, tzinfo=JST))
    assert len(_rows(paths, rk)) == len(got) and not again.get("live", {}).get("settled")   # 二度書かない


def test_real_result_settles_through_ordinary_tick_after_long_stop(env, monkeypatch):
    """本当の結果を取った回の精算が書けないまま（台帳の失敗など）8日止まった場合も、ふつうの見回りで精算する。"""
    paths, col, rk = env
    col.now = lambda: datetime(2025, 8, 30, 16, 10, tzinfo=JST)
    col._save(rk, "result", "result", "/x", "c", result_html([3, 1, 2, 4, 5, 6, 7, 8]))
    col.state["taken"][f"{rk}|result"] = "2025-08-30T16:10:00+09:00"
    col.state.pop("live_pending", None)
    col._write_state()
    _cli_tick(paths, monkeypatch, datetime(2025, 9, 7, 16, 0, tzinfo=JST))
    got = _rows(paths, rk)
    assert got and not [s for s in got if s["status"] == "refunded_void"] and [s for s in got if s.get("hits")]


# ---- 同じ種類（読む順）：予定表から外す途中に、正式の判定が発走時刻を読む --------------------------------------

def test_post_time_survives_archive_between_reads(tmp_path):
    """見回りが「過去の記録に書く → 予定表から外す」をしている間に、正式の判定が発走時刻を読んでも、
    どちらかに載っている発走時刻を使う（出馬表の原本が無いレースでも、予想を「発走不明」にしない）。"""
    root = tmp_path / "live"
    col = Collector(root, client=SimpleNamespace(count=0), now=lambda: datetime(2025, 9, 7, 16, 0, tzinfo=JST))
    rk = "2025-08-30|東京|11"
    col.state["races"][rk] = {"race_date": "2025-08-30", "venue": "東京", "race_number": 11, "post_time": "15:40"}
    col._write_state()
    real = collector_module.read_jsonl

    def archive_meanwhile(path):
        got = real(path)   # 過去の記録を読んだ直後に、見回りが外す（記録に書いてから予定表から外す）
        if not getattr(archive_meanwhile, "done", False):
            archive_meanwhile.done = True
            collector_module.append_lines(root / collector_module.ARCHIVE_FILE, [json.dumps(
                {"race_key": rk, "race_date": "2025-08-30", "venue": "東京", "race_number": 11, "post_time": "15:40"},
                ensure_ascii=False)])
            col.state["races"].pop(rk)
            col._write_state()
        return got
    with patch.object(collector_module, "read_jsonl", archive_meanwhile):
        assert official.post_times(root, {rk}) == {rk: "15:40"}
    assert archive_meanwhile.done


# ---- 💚20261125-18 止める：前の月の月次の書き込みが一度失敗しても、次の見回りで書き直す ---------------------------

@pytest.mark.parametrize("stop_after_fail", [False, True])
def test_previous_month_review_is_retried_until_written(env, monkeypatch, stop_after_fail):
    """8月のレースを9月の見回りで精算し（返還）、直後の8月の月次の書き込みが一度だけ失敗した。次のふつうの見回りで
    （予想・精算が動かない回でも）8月の月次を書き直し、保存した月次の金額が台帳と同じになる。
    stop_after_fail：書き込みの途中で見回りが止まった（状態に残した月を、次の見回りが読む）。"""
    import keiba.review as review
    paths, col, rk = env
    _cancel(col.root, rk)
    col.state["races"][rk].update(list_cname="L", result_links=[["/x", "R"]], known_at="2025-08-30T09:00:00+09:00")
    col._write_state()
    _cli_tick(paths, monkeypatch, datetime(2025, 8, 30, 16, 10, tzinfo=JST), {"R": empty_result(rk)})
    real_write = _REAL_WRITE_REVIEW
    month = "2025-08"
    real_write(paths, month)
    old_json = (paths.data_dir / "review" / f"{month}.json").read_bytes()   # 精算の前の8月の月次（返還0円）
    calls = []

    def writer(p, target, *a):
        calls.append(target)
        if target == month and calls.count(month) == 1:
            if stop_after_fail:
                raise KeyboardInterrupt()              # 見回りがここで止まった（電源・強制終了）
            raise OSError("一時的に書けない")
        return real_write(p, target, *a)

    def tick(at):
        class Cl:
            count = 0
            def get(self, path, cname): return "<html></html>"
            def get_url(self, url): return "<html></html>"
        real = collector_module.Collector
        monkeypatch.setattr(collector_module, "Collector", lambda root: real(root, client=Cl(), now=lambda: at))
        monkeypatch.setattr(real, "refresh", lambda self, force=False: None)
        monkeypatch.setattr(real, "check_notices", lambda self, log=print: None)
        monkeypatch.setattr(cli, "_notify", lambda *a, **k: None)
        monkeypatch.setattr(mam, "export_packets", lambda p: [])
        monkeypatch.setattr(wallet, "run_live", lambda p, **k: _REAL_RUN_LIVE(p, now=at))
        monkeypatch.setattr(review, "write_review", writer)
        out = StringIO()
        try:
            with redirect_stdout(out):
                cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        finally:
            monkeypatch.setattr(collector_module, "Collector", real)
        return json.loads(out.getvalue().splitlines()[-1]) if out.getvalue().strip() else None

    if stop_after_fail:
        with pytest.raises(KeyboardInterrupt):
            tick(datetime(2025, 9, 7, 16, 0, tzinfo=JST))
    else:
        first = tick(datetime(2025, 9, 7, 16, 0, tzinfo=JST))
        assert first["live"]["settled"] > 0
    assert _rows(paths, rk)                                         # 精算は台帳に書けている
    assert month in json.loads((col.root / "state.json").read_text()).get("review_months", [])   # 書けなかった月を残す
    tick(datetime(2025, 9, 7, 16, 5, tzinfo=JST))                   # 次のふつうの見回り（精算は0件）
    assert calls.count(month) >= 2
    assert not json.loads((col.root / "state.json").read_text()).get("review_months")
    saved = json.loads((paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(review.build_review(paths, month), ensure_ascii=False, default=str))
    assert saved["wallets"] == fresh["wallets"] and sum(int(w["returned"]) for w in saved["wallets"]) > 0
    assert (paths.data_dir / "review" / f"{month}.json").read_bytes() != old_json   # 保存した8月の月次も書き直された


# ---- 💚20261125-18 直してから：三連複のタブだけ欠けた回は「取得済み」にしない ----------------------------------

def _replay_trio_tab(directory, missing_until, card_after_odds=False):
    """15:40発走・1時間前の時間帯14:25〜14:40。missing_until より前の回は、オッズの一覧から三連複のタブだけ欠ける。
    card_after_odds：14:25の回は出馬表が取れず（一覧の出馬表リンクが欠け）、オッズだけ取れる。"""
    paths = _paths(directory)
    root = paths.data_dir / "live"
    clock = {"now": T("14:00")}
    told = []

    def fetch(name):
        if name == ENTRY_INDEX[1] or name.startswith("pw01dli"):
            return INDEX
        pages = _pages(clock)
        if name == LIST:
            listing = pages[name].replace("15時41分", "15時40分")
            if card_after_odds and clock["now"] < T("14:30"):
                listing = listing.replace(f"?CNAME={CARD}", "")
            return listing
        if name == "pw15orl10/AA" and missing_until and clock["now"] < T(missing_until):
            return pages[name].splitlines()[0]   # 三連複のタブだけ、まだ無い
        return pages[name]
    client = FakeClient(fetch)

    def tick(hm):
        clock["now"] = T(hm)
        out = StringIO()
        with patch.object(collector_module, "Collector",
                          lambda folder: Collector(folder, client=client, now=lambda: clock["now"])), \
                patch.object(Collector, "check_notices", lambda self, log=print: None), \
                patch.object(cli, "_notify", lambda title, msg: told.append(msg)), \
                patch.object(wallet, "prediction_clock", lambda: clock["now"]), \
                patch.object(Ledger, "_now", lambda _: clock["now"].astimezone(timezone.utc).isoformat(timespec="seconds")), \
                redirect_stdout(out):
            cli.cmd_collect(paths, SimpleNamespace(action="tick"))
        return json.loads(out.getvalue().splitlines()[-1])
    return paths, root, client, tick, told


def test_trio_tab_missing_once_is_recaptured(tmp_path):
    """14:25の回は三連複のタブだけ欠け、14:30から戻る。14:25は取得済みにせず14:30に取り直し、正式の材料がそろう。"""
    paths, root, client, tick, told = _replay_trio_tab(tmp_path / "once", "14:30")
    tick("14:00")
    tick("14:25")
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    tick("14:30")
    assert client.calls.count("pw156ou1/66") == 1                     # 戻った三連複を取り直した
    assert f"{RK}|1h" in Collector(root, client=client).state["taken"]
    assert official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40")) is not None
    last = tick("14:41")
    assert last["missed"] == 0 and not any(f"{RK}|1h" in m for m in told)


def test_trio_tab_missing_through_the_window_is_reported(tmp_path):
    """三連複のタブが時間帯の終わりまで欠けたまま：取得済みにせず、時間帯が過ぎたら取り損ねとして知らせる。
    締切の代わりの予想にはお金を配分しない。"""
    paths, root, client, tick, told = _replay_trio_tab(tmp_path / "always", "16:00")
    for hm in ("14:00", "14:25", "14:30", "14:35", "14:41"):
        last = tick(hm)
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    assert last["missed"] >= 1 and any(f"{RK}|1h" in m for m in told)
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes"), (p["method"], a, v)


def test_card_after_odds_is_not_taken_until_both_are_in_order(tmp_path):
    """14:25はオッズだけ（出馬表リンクが欠け）、14:30に出馬表もオッズも取れる。正式の判定と同じく、出馬表の後の
    三連複がそろった回で取得済みにする（14:25のオッズだけでは取得済みにしない）。"""
    paths, root, client, tick, told = _replay_trio_tab(tmp_path / "order", None, card_after_odds=True)
    tick("14:00")
    tick("14:25")
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    tick("14:30")
    assert f"{RK}|1h" in Collector(root, client=client).state["taken"]
    got = official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40"))
    assert got == T("14:30")
    assert tick("14:41")["missed"] == 0


# ---- 同じ種類（書く途中で止まる）：まむへの入力のファイル -----------------------------------------------------

def test_mam_packet_cut_while_writing_is_written_again(env, monkeypatch):
    """まむへの入力を書く途中で止まっても、半端なファイルを残さない（次の回にもう一度書く）。"""
    from pathlib import Path as _P
    paths, col, rk = env
    real = _P.write_text

    def cut(self, data, *a, **k):
        if self.name.endswith(".json.tmp") or (self.suffix == ".json" and "outbox" in str(self)):
            real(self, data[:20], *a, **k)
            raise KeyboardInterrupt()
        return real(self, data, *a, **k)
    monkeypatch.setattr(_P, "write_text", cut)
    with pytest.raises(KeyboardInterrupt):
        mam.export_packets(paths)
    monkeypatch.setattr(_P, "write_text", real)
    out = paths.data_dir / "mam" / "outbox"
    assert not [f for f in out.glob("*.json")]                       # 半端な .json は残らない
    written = mam.export_packets(paths)
    assert written and all(json.loads(f.read_text(encoding="utf-8"))["race_key"] for f in written)
