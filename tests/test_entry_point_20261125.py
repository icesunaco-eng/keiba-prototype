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
    monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
    monkeypatch.setattr(wallet, "run_live", lambda p, **k: _REAL_RUN_LIVE(p, now=now))
    import keiba.review as review
    monkeypatch.setattr(review, "write_review", lambda p, month, *a, **k: (months if months is not None else []).append(month))
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

@pytest.mark.parametrize("mode", ["write_error", "stop_in_write", "mam_error", "stop_after_settle"])
def test_previous_month_review_is_rewritten_after_any_stop(env, monkeypatch, mode):
    """8月のレースを9月の見回りで精算し（返還を台帳に確定）、その直後に止まった・失敗した。次のふつうの見回りで
    （予想・精算が動かない回でも）8月の月次を書き直し、保存した月次の金額が台帳と同じになる。
    write_error：8月の月次の書き込みが一度失敗。stop_in_write：書き込みの途中で強制終了。
    mam_error：精算の直後、まむへの入力の出力が一時エラー（💚20261125-21 止める①）。stop_after_settle：精算の直後に強制終了。"""
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
    calls, exports = [], []

    def writer(p, target, *a, **k):
        calls.append(target)
        if target == month and calls.count(month) == 1 and mode in ("write_error", "stop_in_write"):
            if mode == "stop_in_write":
                raise KeyboardInterrupt()              # 見回りがここで止まった（電源・強制終了）
            raise OSError("一時的に書けない")
        return real_write(p, target, *a, **k)

    def export(p, **k):
        exports.append(1)
        if len(exports) == 1 and mode == "mam_error":
            raise OSError("まむの出力先が一時的に書けない")
        if len(exports) == 1 and mode == "stop_after_settle":
            raise KeyboardInterrupt()
        return []

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
        monkeypatch.setattr(mam, "export_packets", export)
        monkeypatch.setattr(wallet, "run_live", lambda p, **k: _REAL_RUN_LIVE(p, now=at))
        monkeypatch.setattr(review, "write_review", writer)
        out = StringIO()
        try:
            with redirect_stdout(out):
                cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        finally:
            monkeypatch.setattr(collector_module, "Collector", real)
        return json.loads(out.getvalue().splitlines()[-1]) if out.getvalue().strip() else None

    if mode in ("stop_in_write", "stop_after_settle"):
        with pytest.raises(KeyboardInterrupt):
            tick(datetime(2025, 9, 7, 16, 0, tzinfo=JST))
    else:
        tick(datetime(2025, 9, 7, 16, 0, tzinfo=JST))
    assert _rows(paths, rk)                                         # 精算は台帳に確定している
    assert month in review.stale_months(paths)                      # 月次が古いことを台帳と目印から見つけられる
    assert (paths.data_dir / "review" / f"{month}.json").read_bytes() == old_json
    tick(datetime(2025, 9, 7, 16, 5, tzinfo=JST))                   # 次のふつうの見回り
    assert month not in review.stale_months(paths)
    saved = json.loads((paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(review.build_review(paths, month), ensure_ascii=False, default=str))
    assert saved["wallets"] == fresh["wallets"] and sum(int(w["returned"]) for w in saved["wallets"]) > 0
    before = calls.count(month)
    tick(datetime(2025, 9, 7, 16, 10, tzinfo=JST))                  # 追いついたあとは書き直さない
    assert calls.count(month) == before


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
    real = mam._write_complete

    def cut(tmp, text):   # PR #18 の書き方（書き終えてから一度だけ置く）を、書く途中で止める
        _P(tmp).write_text(text[:20], encoding="utf-8")
        raise KeyboardInterrupt()
    monkeypatch.setattr(mam, "_write_complete", cut)
    with pytest.raises(KeyboardInterrupt):
        mam.export_packets(paths)
    monkeypatch.setattr(mam, "_write_complete", real)
    out = paths.data_dir / "mam" / "outbox"
    assert not [f for f in out.glob("*.json")]                       # 半端な .json は残らない
    written = mam.export_packets(paths)
    assert written and all(json.loads(f.read_text(encoding="utf-8"))["race_key"] for f in written)


# ---- 💚20261125-21 止める②：見回りと手で出す export が重なっても、まむへの正式の入力を壊さない --------------------

def test_mam_packet_overlapping_exports_keep_a_complete_file(env, monkeypatch):
    """見回りの export が一時ファイルを書き終えて正式の名前に置く前に、手で出した export が割り込んで先に置いた。
    どちらの一時ファイルも別々なので混ざらず、正式のファイルは完全な JSON のまま。後から来た側は上書きしない。"""
    import os as _os
    paths, col, rk = env
    real_link = _os.link
    inner = []

    def link_after_other(src, dst):
        if not inner:   # 最初の export が置く直前に、もう1つの export が最後まで走る
            inner.append(None)
            inner[0] = mam.export_packets(paths)
        return real_link(src, dst)
    monkeypatch.setattr(mam.os, "link", link_after_other)
    outer = mam.export_packets(paths)
    monkeypatch.setattr(mam.os, "link", real_link)
    out = paths.data_dir / "mam" / "outbox"
    files = sorted(out.glob("*.json"))
    assert files and inner and inner[0]                              # 割り込んだ側が先に置いた
    for f in files:
        assert json.loads(f.read_text(encoding="utf-8"))["race_key"]  # どれも完全な JSON
    assert not [x for x in out.iterdir() if x.name.endswith(".tmp")]   # 一時ファイルは残らない
    assert not set(outer) & set(inner[0])                            # 同じファイルを二重に「書いた」としない
    again = mam.export_packets(paths)
    assert again == []                                               # 既にあるものは書き直さない


# ---- 💚20261125-21 直してから：倍率の欄が空の三連複のページ・未発表と出ていない未発売は材料に数えない -----------

def _replay_odds_page(directory, mode):
    """mode：empty_cells（14:25の三連複のページは表があるのに倍率の欄が空、14:30から完全）、complete（対照）。"""
    import re as _re
    paths = _paths(directory)
    root = paths.data_dir / "live"
    clock = {"now": T("14:00")}
    told = []

    def fetch(name):
        if name == ENTRY_INDEX[1] or name.startswith("pw01dli"):
            return INDEX
        pages = _pages(clock)
        if name == LIST:
            return pages[name].replace("15時41分", "15時40分")
        if name == "pw156ou1/66" and mode == "empty_cells" and clock["now"] < T("14:30"):
            html, n = _re.subn(r"<td>[0-9.,]+</td>", "<td></td>", pages[name])
            assert n > 3
            return html
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


@pytest.mark.parametrize("mode", ["empty_cells", "complete"])
def test_trio_page_with_empty_cells_is_recaptured(tmp_path, mode):
    paths, root, client, tick, told = _replay_odds_page(tmp_path / mode, mode)
    tick("14:00")
    tick("14:25")
    taken = f"{RK}|1h" in Collector(root, client=client).state["taken"]
    assert taken == (mode == "complete")                              # 空の倍率のページでは取得済みにしない
    got = official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40"))
    assert (got == T("14:25")) == (mode == "complete")
    tick("14:30")
    assert client.calls.count("pw156ou1/66") == (2 if mode == "empty_cells" else 1)   # 戻ったページを取り直した
    assert official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40")) == \
        (T("14:30") if mode == "empty_cells" else T("14:25"))
    assert tick("14:41")["missed"] == 0 and not any(f"{RK}|1h" in m for m in told)
    src = Source(paths, "live")
    p = src.preds[RK][("ml_lgbm", "1h")]
    assert p["odds_gate"]["pass"]                                    # 完全なページで判定した（空のページで固定しない）


@pytest.mark.parametrize("cell, counted", [("未発表", True), (None, False), ("発売中止", False)])
def test_unavailable_odds_counts_only_when_list_says_not_released(tmp_path, cell, counted):
    """前日（24時間前）の時点で一覧にオッズのリンクが無い。オッズの欄に「未発表」と出ているときだけ未発売として
    材料に数える。欄そのものが無い・別の文字なら材料に数えず、時間帯の間は取り直す。"""
    from tests.test_live_collector import race_list
    day_before = lambda hm: datetime.fromisoformat(f"2026-11-21T{hm}:00+09:00")
    root = tmp_path / "live"
    clock = {"now": day_before("15:00")}

    def page(name):
        if name.startswith("pw01dli"):
            return INDEX
        if name == LIST:
            listing = race_list(odds=False).replace("15時41分", "15時40分").replace("18頭", "8頭")
            if cell is None:
                listing = listing.replace('<td class="odds">未発表</td>', "")
            elif cell != "未発表":
                listing = listing.replace('<td class="odds">未発表</td>', f'<td class="odds">{cell}</td>')
            return listing
        return _pages(clock)[name]
    col = Collector(root, client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "known_at": day_before("09:00").isoformat(), "odds_status": "未発表"}
    col.state["refreshed_at"] = day_before("15:10").isoformat()
    col._write_state()
    clock["now"] = day_before("15:26")
    col.capture(RK, "24h")
    assert (f"{RK}|24h" in col.state["taken"]) == counted
    dl = deadline(DAY, "15:40", "24h")
    assert (official.material_time(official.capture_records(root).get((RK, "24h"), []), dl) is not None) == counted
    # 前日の時点だけ記録がある（1時間前の記録はまだ0件）。途中の印の行があっても、レースを丸ごと保留にしない
    held = {}
    _, _, inputs = wallet.live_tables(root, col.state, wallet._snapshots(root), held)
    assert not held and (RK, "24h") in inputs, held


# ---- 同じ種類（画面が読むファイルを書く途中）：health.json・held.json は置き換えで書く ---------------------------

def test_files_read_by_app_are_never_written_in_place(env, monkeypatch):
    """見回りが health.json・held.json を書く途中に確認アプリが読んでも、半端な中身（「読めない」の誤表示）を見ない。
    正式の名前へは直接書かず、書き終えた一時ファイルから置き換える。"""
    from pathlib import Path as _P
    paths, col, rk = env
    real = _P.write_text

    def no_direct(self, *a, **k):
        assert self.name not in ("health.json", "held.json", "odds_check.csv"), f"直接書いた：{self.name}"
        return real(self, *a, **k)
    monkeypatch.setattr(_P, "write_text", no_direct)
    _cli_tick(paths, monkeypatch, datetime(2025, 8, 30, 14, 0, tzinfo=JST))
    assert json.loads((col.root / "health.json").read_text(encoding="utf-8"))["checked_at"]
    assert (col.root / "held.json").exists()



def test_review_writes_one_at_a_time(env):
    """月次は1つずつ書く：別の書き手が鍵を持つ間は待つ（古い中身が後から上書きし、目印だけ新しくなるのを防ぐ）。"""
    import fcntl
    import threading
    from keiba import review
    paths, col, rk = env
    out = paths.data_dir / "review"
    out.mkdir(parents=True, exist_ok=True)
    done = threading.Event()
    with open(out / ".lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        th = threading.Thread(target=lambda: (_REAL_WRITE_REVIEW(paths, "2025-08"), done.set()))
        th.start()
        assert not done.wait(1.0)                                    # 鍵を持つ間は書かない
    th.join(30)
    assert done.is_set() and (out / "2025-08.json").exists()
    assert "2025-08" not in review.stale_months(paths)


# ---- 💚20261126-2（みにの再検証）の指摘 ---------------------------------------------------------------------

def test_review_of_month_without_settlements_is_retried(env, monkeypatch):
    """回帰①：精算の無い月（今日の月）の月次の書き込みが一度失敗しても、次のふつうの見回り（予想・精算が動かない回）で
    書き直す。精算の行数では見つけられない月も、書く前に残した月（review_months）から書き直す。"""
    import keiba.review as review
    from datetime import date as _date
    paths, col, rk = env
    _cancel(col.root, rk)
    col.state["races"][rk].update(list_cname="L", result_links=[["/x", "R"]], known_at="2025-08-30T09:00:00+09:00")
    col._write_state()
    _cli_tick(paths, monkeypatch, datetime(2025, 8, 30, 16, 10, tzinfo=JST), {"R": empty_result(rk)})
    today = _date.today().strftime("%Y-%m")                         # 見回りが月次を書く「今日の月」（精算は無い）
    calls = []

    def writer(p, target, *a, **k):
        calls.append(target)
        if target == today and calls.count(today) == 1:
            raise OSError("一時的に書けない")
        return _REAL_WRITE_REVIEW(p, target, *a, **k)

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
        monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
        monkeypatch.setattr(wallet, "run_live", lambda p, **k: _REAL_RUN_LIVE(p, now=at))
        monkeypatch.setattr(review, "write_review", writer)
        out = StringIO()
        try:
            with redirect_stdout(out):
                cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        finally:
            monkeypatch.setattr(collector_module, "Collector", real)

    tick(datetime(2025, 9, 7, 16, 0, tzinfo=JST))                   # 8月の返還を精算。今日の月の月次は一度失敗
    assert today in calls and not (paths.data_dir / "review" / f"{today}.json").exists()
    assert today in json.loads((col.root / "state.json").read_text())["review_months"]
    assert today not in review.stale_months(paths)                  # 精算の行数では見つけられない月
    tick(datetime(2025, 9, 7, 16, 5, tzinfo=JST))                   # 次のふつうの見回り（精算0件）
    assert (paths.data_dir / "review" / f"{today}.json").exists() and (paths.data_dir / "review" / f"{today}.html").exists()
    assert not json.loads((col.root / "state.json").read_text()).get("review_months")


def test_mam_packet_on_storage_without_hard_links(env, monkeypatch):
    """回帰②：ハードリンクを作れない置き場（ENOTSUP）でも、まむの入力を「上書きしない移動」で置く。
    手で出す export が割り込んでも、先に置いた完全な入力を上書きしない。"""
    import errno
    import os as _os
    paths, col, rk = env

    def no_link(src, dst):
        raise OSError(errno.ENOTSUP, "Operation not supported")
    monkeypatch.setattr(mam.os, "link", no_link)
    real_move = mam._rename_noreplace
    inner = []

    def move_after_other(src, dst):
        if not inner:
            inner.append(None)
            inner[0] = mam.export_packets(paths)                     # 置く直前に、もう1つの export が最後まで走る
        return real_move(src, dst)
    monkeypatch.setattr(mam, "_rename_noreplace", move_after_other)
    outer = mam.export_packets(paths)
    out = paths.data_dir / "mam" / "outbox"
    files = sorted(out.glob("*.json"))
    assert files and inner[0] and not set(outer) & set(inner[0])     # 先に置いた側だけが「書いた」
    for f in files:
        assert json.loads(f.read_text(encoding="utf-8"))["race_key"]
    assert not [x for x in out.iterdir() if x.name.endswith(".tmp")]
    monkeypatch.setattr(mam, "_rename_noreplace", real_move)
    assert mam.export_packets(paths) == []                            # 既にあるものは書き直さない


def _replay_trio_rows(directory, mode):
    """mode：row_missing（14:25は1-2-3の行が丸ごと欠け、14:30から完全）、display_row（馬番の無い表示だけの空の行が1行ある）。
    card_short：14:30より前は出馬表が途中まで（8番の行が無い・一覧は8頭）。card_short_always：締切まで途中のまま。"""
    import re as _re
    paths = _paths(directory)
    root = paths.data_dir / "live"
    clock = {"now": T("14:00")}
    told = []

    def fetch(name):
        if name == ENTRY_INDEX[1] or name.startswith("pw01dli"):
            return INDEX
        pages = _pages(clock)
        if name == LIST:
            return pages[name].replace("15時41分", "15時40分")
        if name == CARD and mode == "card_preparing" and clock["now"] < T("14:30"):
            return _re.sub(r'<td class="num">\d+</td>', '<td class="num"></td>', pages[name])   # 出馬表が準備中（馬番が空）
        if name == CARD and (mode == "card_short_always" or (mode == "card_short" and clock["now"] < T("14:30"))
                             or (mode == "short_at_1430" and T("14:30") <= clock["now"] < T("14:35"))):
            rows = _re.findall(r"<tr>.*?</tr>", pages[name], flags=_re.S)
            assert len(rows) == 8
            return pages[name].replace(rows[-1], "")   # 出馬表が途中まで（8番の行が無い。一覧は8頭）
        if name == "pw156ou1/66" and clock["now"] < T("14:30"):
            html = pages[name]
            if mode in ("row_missing", "card_preparing"):
                html, n = _re.subn(r'(<caption>1-2</caption><tbody>)<tr><th scope="row">3</th><td>[^<]*</td></tr>', r"\1", html)
                assert n == 1
            elif mode == "display_row":
                html = html.replace("<tbody>", "<tbody><tr><th>（表示だけの行）</th><td></td></tr>", 1)
            elif mode == "extra_td":   # 馬番の行に、空の倍率の欄と別の「更新中」の欄が並ぶ
                html, n = _re.subn(r'(<caption>1-2</caption><tbody><tr><th scope="row">3</th>)<td>[^<]*</td>',
                                   r"\1<td></td><td>更新中</td>", html)
                assert n == 1
            return html
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


@pytest.mark.parametrize("mode, taken_at_1425", [("row_missing", False), ("display_row", True),
                                                  ("card_preparing", False), ("extra_td", False)])
def test_trio_page_rows_are_checked_against_the_roster(tmp_path, mode, taken_at_1425):
    """直してから②：14:25の三連複のページで1-2-3の行が丸ごと欠けた → 取得済みにせず、14:30に取り直す。
    回帰③：馬番の無い表示だけの空の行は空欄の検査に入れない（14:25のまま取得済み）。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / mode, mode)
    tick("14:00")
    tick("14:25")
    assert (f"{RK}|1h" in Collector(root, client=client).state["taken"]) == taken_at_1425
    tick("14:30")
    assert client.calls.count("pw156ou1/66") == (1 if taken_at_1425 else 2)
    assert official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40")) == \
        (T("14:25") if taken_at_1425 else T("14:30"))
    assert tick("14:41")["missed"] == 0 and not any(f"{RK}|1h" in m for m in told)


def test_race_missing_from_list_does_not_reuse_old_unreleased(tmp_path):
    """直してから③：前日の時点で、さっきまで「未発表」だったレースが今回の一覧から一時的に消えた。前の回の「未発表」を
    使わず材料に数えない（取り直す）。次の回に一覧に戻って「未発表」と出たら材料に数える。"""
    from tests.test_live_collector import race_list
    day_before = lambda hm: datetime.fromisoformat(f"2026-11-21T{hm}:00+09:00")
    root = tmp_path / "live"
    clock = {"now": day_before("15:00")}
    gone = {"on": True}

    def page(name):
        if name.startswith("pw01dli"):
            return INDEX
        if name == LIST:
            listing = race_list(odds=False).replace("15時41分", "15時40分").replace("18頭", "8頭")
            return listing.replace('alt="11レース"', 'alt="12レース"') if gone["on"] else listing   # 11Rの行が一時的に無い
        return _pages(clock)[name]
    col = Collector(root, client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "card_cname": CARD, "known_at": day_before("09:00").isoformat(), "odds_status": "未発表"}
    col._write_state()
    clock["now"] = day_before("15:26")
    from keiba.live import jra as _jra
    fresh = {x["race_number"] for x in _jra.parse_race_list(page(LIST))["races"]}
    assert 11 not in fresh                                          # 今回の一覧に11Rの行が無い
    col.capture(RK, "24h")
    assert f"{RK}|24h" not in col.state["taken"]
    gone["on"] = False
    clock["now"] = day_before("15:31")
    col.capture(RK, "24h")
    assert f"{RK}|24h" in col.state["taken"]



# ---- 💚20261126-5（みにの再検証）の指摘 ---------------------------------------------------------------------

def test_month_end_stop_right_after_live_started_keeps_the_month(env, monkeypatch):
    """止める：月末23:55の見回りが予想・精算を始める印を書いた直後に止まり、丸1日以上あけて翌々日に再開した。
    書く予定だった月（8月）は、始める印と同じ保存で残っているので、再開した見回りが8月の月次を書く。"""
    import keiba.review as review
    paths, col, rk = env
    col.state["live_pending"] = True
    col._write_state()
    stopped = {"on": True}

    def run(p, **k):
        if stopped["on"]:
            stopped["on"] = False
            raise KeyboardInterrupt()   # 始める印を書いた直後に止まった（電源・強制終了）
        return _REAL_RUN_LIVE(p, now=k.get("now"))
    monkeypatch.setattr(review, "write_review", _REAL_WRITE_REVIEW)

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
        monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
        monkeypatch.setattr(wallet, "run_live", lambda p, **k: run(p, now=at))
        out = StringIO()
        try:
            with redirect_stdout(out):
                cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        finally:
            monkeypatch.setattr(collector_module, "Collector", real)

    with pytest.raises(KeyboardInterrupt):
        tick(datetime(2025, 8, 31, 23, 55, tzinfo=JST))
    assert "2025-08" in json.loads((col.root / "state.json").read_text())["live_months"]   # 始める印と同じ保存
    assert not (paths.data_dir / "review" / "2025-08.json").exists()
    tick(datetime(2025, 9, 2, 10, 0, tzinfo=JST))                   # 翌々日に再開（9/1は動いていない）
    for f in ("2025-08.json", "2025-08.html"):
        assert (paths.data_dir / "review" / f).exists(), f
    assert "2025-08" not in (json.loads((col.root / "state.json").read_text()).get("review_months") or [])


def test_mam_packets_through_ordinary_ticks_without_hard_links(tmp_path, monkeypatch):
    """あとで②：ハードリンクを作れない置き場（ENOTSUP）で、ふつうの見回りの1日と、書き込みの直前ごとに手で動かす
    mam export を重ねても、まむの入力は止めない1日と同じ（完全な JSON・同じファイル）になる。"""
    import errno
    from tests.crash_harness import exporter, invariants, run_day
    base = run_day(tmp_path / "base")
    want = json.loads(json.dumps(invariants(base), default=str))

    def no_link(src, dst):
        raise OSError(errno.ENOTSUP, "Operation not supported")
    monkeypatch.setattr(mam.os, "link", no_link)
    day = run_day(tmp_path / "nolink", models=base.root / "models", probe=exporter)
    assert not day.probe.errors
    got = json.loads(json.dumps(invariants(day), default=str))
    assert got["mam_files"] == want["mam_files"] and got["mam_json_complete"]
    assert got == want


def test_race_missing_from_list_through_ordinary_ticks(tmp_path):
    """あとで②：前日の時点で、さっきまで「未発表」だったレースが今回の一覧から一時的に消えた回を、ふつうの見回りで通す。
    前の回の「未発表」を使わず取得済みにせず、一覧に戻った回に取得済みにする。取り損ねにはしない。"""
    from tests.test_live_collector import race_list
    day_before = lambda hm: datetime.fromisoformat(f"2026-11-21T{hm}:00+09:00")
    paths = _paths(tmp_path)
    root = paths.data_dir / "live"
    clock = {"now": day_before("15:00")}
    told = []

    def fetch(name):
        if name == ENTRY_INDEX[1] or name.startswith("pw01dli"):
            return INDEX
        if name == LIST:
            listing = race_list(odds=False).replace("15時41分", "15時40分").replace("18頭", "8頭")
            if day_before("15:24") <= clock["now"] < day_before("15:30"):
                listing = listing.replace('alt="11レース"', 'alt="12レース"')   # 11Rの行が一時的に無い
            return listing
        return _pages(clock)[name]
    client = FakeClient(fetch)

    def tick(at):
        clock["now"] = at
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
    tick(day_before("15:00"))
    tick(day_before("15:26"))                                       # 一覧に11Rが無い回
    assert f"{RK}|24h" not in Collector(root, client=client).state["taken"]
    tick(day_before("15:31"))                                       # 一覧に戻り「未発表」
    assert f"{RK}|24h" in Collector(root, client=client).state["taken"]
    assert tick(day_before("15:41"))["missed"] == 0 and not any(f"{RK}|24h" in m for m in told)



# ---- 💚20261127-1（みにの再検証）の指摘 ---------------------------------------------------------------------

def _mam_1h(paths):
    f = paths.data_dir / "mam" / "outbox" / f"{RK.replace('|', '_')}_1h.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def test_short_card_is_recaptured_before_prediction_and_mam(tmp_path):
    """止める：14:25は一覧8頭・出馬表7頭（8番の行が無い）。三連複は8頭分そろっている。14:25は取得済みにせず、
    予想・まむの入力も作らない。14:30に完全な出馬表（3番取消）を取り直し、その時点の正式の予想とまむの入力を作る。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "short", "card_short")
    tick("14:00")
    tick("14:25")
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    recs = [json.loads(x) for x in (root / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()]
    trio = [x for x in recs if x["kind"] == "odds_trio" and x["time_point"] == "1h"]
    assert len(trio) == 1 and "名簿" in trio[0]["incomplete"] and "頭数" in trio[0]["incomplete"]
    assert _mam_1h(paths) is None                                    # 欠けた名簿の入力を固定しない
    led = Ledger(paths.ledger_path, read_only=True)
    assert not [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    tick("14:30")
    assert client.calls.count("pw156ou1/66") == 2                    # 取り直した
    assert f"{RK}|1h" in Collector(root, client=client).state["taken"]
    assert official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40")) == T("14:30")
    packet = _mam_1h(paths)
    assert packet["race"]["field_size"] == 8 and packet["scratched_known"] == [3]
    assert [h["horse_number"] for h in packet["horses"]] == [1, 2, 4, 5, 6, 7, 8]
    src = Source(paths, "live")
    p = src.preds[RK][("ml_lgbm", "1h")]
    assert p["odds_gate"]["pass"] and not p["odds_gate"].get("card_issue")
    assert tick("14:41")["missed"] == 0 and not any(f"{RK}|1h" in m for m in told)


def test_short_card_through_the_deadline_is_missed_and_not_sent_to_mam(tmp_path):
    """止める：出馬表が締切まで途中のまま（一覧8頭・出馬表7頭）。取得済みにせず、時間帯が過ぎたら取り損ねとして知らせる。
    画面の代わりの予想にはお金を配分せず、欠けた名簿のまむの入力は出さない。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "always", "card_short_always")
    for hm in ("14:00", "14:25", "14:30", "14:35", "14:41", "14:46"):
        last = tick(hm)
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    assert last["missed"] >= 1 and any(f"{RK}|1h" in m for m in told)
    assert _mam_1h(paths) is None
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes"), (p["method"], a, v)


@pytest.mark.parametrize("short", [False, True, "before_draw", "table0", "nested", "blank7", "blank8_scratch",
                                   "blank9_scratch"])
def test_unreleased_odds_with_short_card_is_not_counted(tmp_path, short):
    """前日の時点で一覧に「未発表」と出ていても、同じ回の出馬表が途中まで（一覧8頭・出馬表7頭）なら材料に数えない。
    完全な出馬表なら今までどおり未発売として数える。馬番が全部空（枠順の確定前。本番の前日朝の1Rで実例）も
    今までどおり数える（予想の側もこの時点の入力を作らない）。
    止める2（💚20261127-5）：出馬表の表が無い・入れ子・全部空でも7行（一覧8頭）は読めない出馬表なので数えない。
    全部空の8行に取消の印がある（本番の原本にもある形）は枠順の確定前として数える。枠順の前の取消で馬番の無い取消の行が
    残り、全部の行が9・取消でない行が8（一覧8頭）も枠順の確定前として数える（みにの台本の形）。"""
    import re as _re
    from tests.test_live_collector import race_list
    day_before = lambda hm: datetime.fromisoformat(f"2026-11-21T{hm}:00+09:00")
    root = tmp_path / "live"
    clock = {"now": day_before("15:00")}

    def page(name):
        if name.startswith("pw01dli"):
            return INDEX
        if name == LIST:
            return race_list(odds=False).replace("15時41分", "15時40分").replace("18頭", "8頭")
        html = _pages(clock)[name]
        if name == CARD and short == "blank9_scratch":
            from tests.test_capture_window_shift import ids as _ids
            html = card_html([_ids(i) for i in range(9)]).replace(
                '<td class="num">3</td>', '<td class="num"><span class="cap">取消</span></td>', 1)
            html = _re.sub(r'<td class="num">\d+</td>', '<td class="num"></td>', html)
        elif name == CARD and short in ("before_draw", "blank7", "blank8_scratch"):
            html = _re.sub(r'<td class="num">\d+</td>', '<td class="num"></td>', html)
            if short == "blank7":
                html = html.replace(_re.findall(r"<tr>.*?</tr>", html, flags=_re.S)[-1], "")
            if short == "blank8_scratch":
                html = html.replace('<td class="num"></td>', '<td class="num"><span class="cap">取消</span></td>', 1)
        elif name == CARD and short == "table0":
            html = "<html><body><p>出馬表を準備中</p></body></html>"
        elif name == CARD and short == "nested":
            html = f"<table><tr><td>{html}</td></tr></table>"
        elif name == CARD and short:
            html = html.replace(_re.findall(r"<tr>.*?</tr>", html, flags=_re.S)[-1], "")
        return html
    col = Collector(root, client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "known_at": day_before("09:00").isoformat(), "odds_status": "未発表"}
    col.state["refreshed_at"] = day_before("15:10").isoformat()
    col._write_state()
    clock["now"] = day_before("15:26")
    col.capture(RK, "24h")
    counted = short in (False, "before_draw", "blank8_scratch", "blank9_scratch")
    assert (f"{RK}|24h" in col.state["taken"]) == counted
    dl = deadline(DAY, "15:40", "24h")
    assert (official.material_time(official.capture_records(root).get((RK, "24h"), []), dl) is not None) == counted


def test_tick_does_not_wait_for_manual_review_lock(env, monkeypatch):
    """直してから：手で動かす月次（keiba review）が出力の鍵を持っている間に見回りが月次を書く回になっても、
    収集の鍵を持ったまま待たない。書けなかった月は review_months に残し（失敗として知らせない）、次の見回りが
    収集の鍵を取れる。鍵が外れた後の見回りで月次を書く。"""
    import fcntl
    import threading
    import keiba.review as review
    paths, col, rk = env
    _cancel(col.root, rk)
    col.state["races"][rk].update(list_cname="L", result_links=[["/x", "R"]], known_at="2025-08-30T09:00:00+09:00")
    col._write_state()
    _cli_tick(paths, monkeypatch, datetime(2025, 8, 30, 16, 10, tzinfo=JST), {"R": empty_result(rk)})
    monkeypatch.setattr(review, "write_review", _REAL_WRITE_REVIEW)
    told = []

    def tick(at):
        class Cl:
            count = 0
            def get(self, path, cname): return "<html></html>"
            def get_url(self, url): return "<html></html>"
        real = collector_module.Collector
        monkeypatch.setattr(collector_module, "Collector", lambda root: real(root, client=Cl(), now=lambda: at))
        monkeypatch.setattr(real, "refresh", lambda self, force=False: None)
        monkeypatch.setattr(real, "check_notices", lambda self, log=print: None)
        monkeypatch.setattr(cli, "_notify", lambda *a, **k: told.append(a))
        monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
        monkeypatch.setattr(wallet, "run_live", lambda p, **k: _REAL_RUN_LIVE(p, now=at))
        out = StringIO()
        try:
            with redirect_stdout(out):
                cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        finally:
            monkeypatch.setattr(collector_module, "Collector", real)
        return json.loads(out.getvalue().splitlines()[-1])

    out = paths.data_dir / "review"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ".lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)                             # 手で動かす月次が書いている間
        done = threading.Event()
        th = threading.Thread(target=lambda: (tick(datetime(2025, 9, 7, 16, 0, tzinfo=JST)), done.set()))
        th.start()
        assert done.wait(60), "見回りが月次の鍵を待って止まった"         # 8月の返還を精算する回（月次を書く回）
        th.join()
        state = json.loads((col.root / "state.json").read_text())
        assert "2025-08" in state["review_months"] and not state.get("live_failures")
        assert not (out / "2025-08.json").exists() or "2025-08" in review.stale_months(paths)
        with open(col.root / "tick.lock", "a") as lock:              # 収集の鍵は外れている（次の見回りが動ける）
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
    assert not told
    tick(datetime(2025, 9, 7, 16, 5, tzinfo=JST))                   # 鍵が外れた後のふつうの見回り
    assert (out / "2025-08.json").exists() and "2025-08" not in review.stale_months(paths)
    assert not json.loads((col.root / "state.json").read_text()).get("review_months")


def test_ordinary_live_run_without_settlement_does_not_rewrite_reviews(env, monkeypatch):
    """あとで（負荷）：月次が読むもの（予想・精算・目録・発走時刻の記録など）が何も変わらない予想・精算の回（最後まで
    終わった回）は、月次を作り直さない。始める印と一緒に残した月（live_months）は、最後まで終わったら外す。"""
    import keiba.review as review
    paths, col, rk = env
    months = []

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
        monkeypatch.setattr(mam, "export_packets", lambda p, **k: [])
        monkeypatch.setattr(wallet, "run_live", lambda p, **k: {"predicted": 1, "settled": 0, "months": []})
        monkeypatch.setattr(review, "write_review", lambda p, month, *a, **k: (months.append(month), _REAL_WRITE_REVIEW(p, month, *a, **k)))
        try:
            with redirect_stdout(StringIO()):
                cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))
        finally:
            monkeypatch.setattr(collector_module, "Collector", real)

    tick(datetime(2025, 8, 30, 14, 0, tzinfo=JST))                  # 最初の回（発走時刻の記録が初めて書かれるので月次も書く）
    assert not review.stale_months(paths)
    months.clear()
    col = Collector(col.root)
    col.state["live_pending"] = True
    col._write_state()
    tick(datetime(2025, 8, 30, 14, 5, tzinfo=JST))                  # 予想・精算は動くが、月次が読むものは変わらない回
    state = json.loads((col.root / "state.json").read_text())
    assert months == [] and not state.get("review_months") and "live_months" not in state


def test_review_is_rewritten_when_only_predictions_and_captures_change(tmp_path):
    """直してから（💚20261127-5）：月次を書いた後、精算0件で予想・原本だけ増えたふつうの見回り。月次が読む予想・取得の数が
    変わったので、その回（収集の鍵を待たずに書ける回）に月次を書き直し、保存した月次が今の中身と同じになる。"""
    from keiba import review
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "rv", "display_row")
    tick("14:00")
    month = DAY[:7]
    review.write_review(paths, month)                                # 予想の無い時点の月次（予想0R）
    before = json.loads((paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8"))
    tick("14:25")                                                    # 原本4件・予想あり・精算0件
    saved = json.loads((paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8"))
    now = json.loads(json.dumps(review.build_review(paths, month), ensure_ascii=False, default=str))
    assert before["races_predicted"] == 0 and now["races_predicted"] == 1
    assert saved["races_predicted"] == now["races_predicted"] and saved["comparison"] == now["comparison"]
    assert month not in review.stale_months(paths)
    text = (paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8")
    tick("14:30")                                                    # 何も変わらない回は書き直さない
    assert (paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8") == text


def test_stop_after_complete_round_then_short_card_uses_one_round(tmp_path):
    """止める1（💚20261127-5）：14:25の見回りが8頭の出馬表と三連複を保存した直後、取得済み・予想の前に止まった。14:30の
    再開では出馬表が7頭（三連複は途中）。前の回の完全な組と今の回の途中の出馬表を混ぜず、取得済み・正式の予想を作らない。
    14:35に完全な組を取り直し、その回の出馬表で正式の予想とまむの入力を作る。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "stop", "short_at_1430")
    tick("14:00")
    with patch.object(official, "material_time", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        tick("14:25")                                                # 原本は保存した直後に止まった
    recs = [json.loads(x) for x in (root / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {x["kind"] for x in recs if x["time_point"] == "1h"} >= {"card", "odds_trio"}
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    tick("14:30")                                                    # 出馬表7頭の回
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    assert official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40")) is None
    led = Ledger(paths.ledger_path, read_only=True)
    assert not [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    assert _mam_1h(paths) is None
    tick("14:35")                                                    # 完全な組
    assert f"{RK}|1h" in Collector(root, client=client).state["taken"]
    assert official.material_time(official.capture_records(root)[(RK, "1h")], T("14:40")) == T("14:35")
    packet = _mam_1h(paths)
    assert packet["scratched_known"] == [3] and [h["horse_number"] for h in packet["horses"]] == [1, 2, 4, 5, 6, 7, 8]
    p = Source(paths, "live").preds[RK][("ml_lgbm", "1h")]
    assert p["odds_gate"]["pass"] and not p["odds_gate"].get("card_issue")
    assert tick("14:41")["missed"] == 0


def test_rejected_short_card_then_round_without_card_is_not_counted(tmp_path):
    """止める1の別の形（💚20261127-5）：前日、先の回の7頭の出馬表は弾いた（未発売の印は途中）。次の回は出馬表のリンクが無く
    「未発表」の印だけ。前の回の7頭の出馬表と次の回の印を組み合わせず、取得済みにしない。出馬表が戻った回に取得済みにする。"""
    import re as _re
    from tests.test_live_collector import race_list
    day_before = lambda hm: datetime.fromisoformat(f"2026-11-21T{hm}:00+09:00")
    root = tmp_path / "live"
    clock = {"now": day_before("15:00")}

    def page(name):
        if name.startswith("pw01dli"):
            return INDEX
        if name == LIST:
            listing = race_list(odds=False).replace("15時41分", "15時40分").replace("18頭", "8頭")
            if day_before("15:30") <= clock["now"] < day_before("15:35"):
                listing = listing.replace(f"?CNAME={CARD}", "")      # 一覧の出馬表リンクが一時的に無い回
            return listing
        html = _pages(clock)[name]
        if name == CARD and clock["now"] < day_before("15:30"):
            html = html.replace(_re.findall(r"<tr>.*?</tr>", html, flags=_re.S)[-1], "")
        return html
    col = Collector(root, client=FakeClient(page), now=lambda: clock["now"])
    col.state["races"][RK] = {"race_date": DAY, "venue": "京都", "race_number": 11, "post_time": "15:40",
                             "field_size": 8, "surface": "芝", "distance_m": 1600, "list_cname": LIST,
                             "card_cname": CARD, "known_at": day_before("09:00").isoformat(), "odds_status": "未発表"}
    col.state["refreshed_at"] = day_before("15:10").isoformat()
    col._write_state()
    dl = deadline(DAY, "15:40", "24h")
    clock["now"] = day_before("15:26")
    col.capture(RK, "24h")                                           # 7頭：弾く
    assert f"{RK}|24h" not in col.state["taken"]
    clock["now"] = day_before("15:31")
    col.state["races"][RK].pop("card_cname")                         # 出馬表のリンクが無い回（印だけ）
    col.capture(RK, "24h")
    assert f"{RK}|24h" not in col.state["taken"]
    assert official.material_time(official.capture_records(root).get((RK, "24h"), []), dl) is None
    clock["now"] = day_before("15:36")
    col.state["races"][RK]["card_cname"] = CARD                      # 出馬表が戻った（8頭）
    col.capture(RK, "24h")
    assert f"{RK}|24h" in col.state["taken"]
    assert official.material_time(official.capture_records(root).get((RK, "24h"), []), dl) == day_before("15:36")
