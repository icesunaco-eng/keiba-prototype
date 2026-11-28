"""数え方：材料を取った後、締切＋10分までに最初に保存した版だけ（2026-11-22 決定）。別実装の答えと、画面・仮想財布・月次の3つを照らす。"""
import json
import random
import threading
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from keiba import official
from keiba.config import Paths
from keiba.ledger import Ledger
from keiba.live.collector import JST
from keiba.live.wallet import ALLOC_METHODS, _settle, wallet_summary

DAY = "2026-11-21"
RK = f"{DAY}|京都|1"
POST = datetime(2026, 11, 21, 10, 1, tzinfo=JST)
LIMIT_BEFORE_POST = {"24h": 1430, "1h": 50, "15m": 5}   # 締切＋10分は発走の何分前か


def world(tmp_path, preds, posts=None, settle=True, snaps=()):
    """preds：(レース, 時点, 方式, 版, 作った時刻, 賭け金, 払戻)。"""
    paths = Paths(data_dir=tmp_path / "data")
    live = paths.data_dir / "live"
    live.mkdir(parents=True)
    posts = {RK: "10:01"} if posts is None else posts
    races = {rk: {"race_date": rk[:10], "venue": rk.split("|")[1], "race_number": int(rk.split("|")[2]), "post_time": p,
                  "field_size": 8} for rk, p in posts.items()}
    (live / "state.json").write_text(json.dumps({"races": races}, ensure_ascii=False))
    (live / "none.html").write_text("<html></html>")   # 原本の中身は使わない（目録の時刻だけの試験）。読めない原本の扱いにはしない
    (live / "snapshots.jsonl").write_text("".join(
        json.dumps({"race_key": rk, "time_point": tp, "kind": kind, "fetched_at": at.isoformat(), "path": "none.html",
                    "sha256": "x"}, ensure_ascii=False) + "\n" for rk, tp, kind, at in snaps))
    led = Ledger(paths.ledger_path)
    for rk, tp, m, v, at, stake, back in preds:
        led._now = lambda at=at: at.astimezone(timezone.utc).isoformat(timespec="seconds")
        alloc = {a: {"status": "allocated", "stakes": {"1-2-3": stake}, "total": stake} for a in ALLOC_METHODS}
        led.add_prediction(rk, tp, m, v, {"race_date": rk[:10], "period": "live", "allocation": alloc, "horses": [],
                                          "trios": [{"combo": "1-2-3", "p": 0.1}]}, "i", "m", "c")
        if settle and tp != "24h":
            for a in ALLOC_METHODS:
                led.add_settlement(rk, tp, m, v, a, {"status": "settled", "stake": stake, "returned": back,
                                                     "hits": ["1-2-3"] if back else []})
    led.commit()
    led.close()
    return paths


def totals_everywhere(paths):
    from keiba.app.data import Source
    from keiba.app.pages import wallet_table
    from keiba.review import build_review

    def rows(records):
        return {(r["method"], r["wallet"]): (int(r["races"]), int(r["bet_races"]), int(r["stake"]), int(r["returned"]))
                for r in records}
    src = Source(paths, "live")
    app = {(x["method"], a): (x["races"], x["bet"], x["stake"], x["returned"]) for a in ALLOC_METHODS
           for x in wallet_table(src, a)}
    return (app, rows(wallet_summary(paths).to_dict("records")),
            rows(build_review(paths, DAY[:7])["wallets"]), src)


@pytest.mark.parametrize("seed", range(120))
def test_random_ledgers_match_independent_answer(tmp_path, seed):
    rnd = random.Random(seed)
    posts = {f"{DAY}|{v}|{n}": rnd.choice(["00:30", "09:45", "10:01", "15:40", "23:59", None])
             for v in ("京都", "福島") for n in range(1, rnd.randint(2, 5))}
    preds, seen, snaps, ready = [], set(), [], {}
    for rk in sorted(posts):
        for tp, before in LIMIT_BEFORE_POST.items():
            if not posts[rk]:
                continue
            dl = datetime.fromisoformat(f"{DAY}T{posts[rk]}:00+09:00") - timedelta(minutes=before + 10)
            case = rnd.choice(["complete", "unsold", "none", "card_only", "late_odds", "odds_first", "retry", "just", "subsec"])
            c, o = dl - timedelta(minutes=12), dl - timedelta(minutes=10)
            got = {"complete": [("card", c), ("odds_trio", o)], "unsold": [("card", c), ("odds_unavailable", o)],
                   "none": [], "card_only": [("card", c), ("odds_first", o)],
                   "late_odds": [("card", c), ("odds_trio", dl + timedelta(seconds=1))],
                   "odds_first": [("odds_trio", c), ("card", o)],
                   "retry": [("odds_trio", c - timedelta(minutes=1)), ("card", c), ("odds_trio", o), ("odds_trio", dl)],
                   "just": [("card", c), ("odds_trio", dl)],
                   "subsec": [("card", c), ("odds_trio", o + timedelta(microseconds=400000))]}[case]
            snaps += [(rk, tp, k, at) for k, at in got]
            ready[(rk, tp)] = o if case in ("complete", "unsold", "retry", "subsec") else dl
    for _ in range(rnd.randint(1, 45)):
        rk = rnd.choice(sorted(posts))
        key = (rk, rnd.choice(["24h", "1h", "15m"]), rnd.choice(["ml_lgbm", "int_market_ml"]), f"live-{rnd.randint(0, 6):08d}")
        if key in seen:
            continue
        seen.add(key)
        hhmm = posts[rk] or "12:00"
        post = datetime.fromisoformat(f"{DAY}T{hhmm}:00+09:00")
        limit = post - timedelta(minutes=LIMIT_BEFORE_POST[key[1]])
        at = limit + timedelta(seconds=rnd.choice([-90000, -1201, -1200, -1199, -601, -600, -599, -60, -1, -1, 0, 0, 1, 60, 7200]))
        preds.append((*key, at, rnd.randint(1, 90) * 100, rnd.choice([0, 0, 1200, 45600])))
    paths = world(tmp_path, preds, posts, snaps=snaps)

    shown = {}                                  # 別実装：書いた順に見て、期限内で一番早い時刻を残す
    for rk, tp, m, v, at, stake, back in preds:
        if posts[rk] and at <= datetime.fromisoformat(f"{DAY}T{posts[rk]}:00+09:00") - timedelta(minutes=LIMIT_BEFORE_POST[tp]) \
                and at >= ready[(rk, tp)]:
            if (rk, m, tp) not in shown or at < shown[(rk, m, tp)][0]:
                shown[(rk, m, tp)] = (at, v, stake, back)
    want = defaultdict(lambda: [0, 0, 0, 0])
    for rk, m in {(rk, m) for rk, m, _ in shown}:
        pick = shown.get((rk, m, "15m")) or shown.get((rk, m, "1h"))
        if pick:
            for a in ALLOC_METHODS:
                x = want[(m, a)]
                x[0] += 1
                x[1] += 1
                x[2] += pick[2]
                x[3] += pick[3]
    want = {k: tuple(v) for k, v in want.items()}
    app, wallet, review, src = totals_everywhere(paths)
    assert app == want and wallet == want and review == want
    assert {(rk, m, tp): p["version"] for rk, ps in src.preds.items() for (m, tp), p in ps.items()} == \
        {k: v[1] for k, v in shown.items()}


def test_seventeen_versions_count_as_one_race(tmp_path):
    preds = [(RK, "15m", "ml_lgbm", f"live-{i:08d}", POST + timedelta(minutes=i - 12), 100 * (i + 1), 0) for i in range(17)]
    app, wallet, review, src = totals_everywhere(world(tmp_path, preds))
    assert app == wallet == review and app[("ml_lgbm", "kelly")] == (1, 1, 100, 0)   # 期限内で最初の版（1番目）
    assert src.excluded == {"before_material": 0, "after_limit": 9, "post_unknown": 0}


def test_newer_24h_only_version_does_not_displace_15m(tmp_path):
    preds = [(RK, "15m", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(minutes=10), 700, 0),
             (RK, "24h", "ml_lgbm", "live-bbbbbbbb", POST - timedelta(hours=23, minutes=58), 900, 0)]
    app, wallet, review, _ = totals_everywhere(world(tmp_path, preds))
    assert app == wallet == review and app[("ml_lgbm", "kelly")][2] == 700


def test_post_time_from_archive_and_from_card(tmp_path):
    preds = [(RK, "15m", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(minutes=10), 700, 0)]
    paths = world(tmp_path, preds, posts={})
    live = paths.data_dir / "live"
    assert not wallet_summary(paths).to_dict("records")
    (live / "races_archive.jsonl").write_text(json.dumps({"race_key": RK, "post_time": "10:01"}, ensure_ascii=False) + "\n")
    assert int(wallet_summary(paths)["stake"].iloc[0]) == 700
    (live / "races_archive.jsonl").write_text("")
    (live / "card.html").write_text("<html><body><p>発走時刻：<strong>10時01分</strong></p></body></html>")
    (live / "snapshots.jsonl").write_text(json.dumps({"race_key": RK, "kind": "card", "time_point": "15m", "path": "card.html",
                                                      "fetched_at": "2026-11-21T09:50:00+09:00"}, ensure_ascii=False) + "\n")
    assert int(wallet_summary(paths)["stake"].iloc[0]) == 700


def test_late_prediction_is_not_settled_and_early_one_is(tmp_path):
    preds = [(RK, "1h", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(minutes=50), 500, 0),
             (RK, "15m", "ml_lgbm", "live-aaaaaaaa", POST + timedelta(minutes=40), 900, 0),
             (RK, "15m", "ml_lgbm", "live-bbbbbbbb", POST + timedelta(minutes=50), 800, 0)]
    paths = world(tmp_path, preds, settle=False)
    live = paths.data_dir / "live"
    res = {"rows": [{"horse_number": n, "finish_position": n, "status": "finished"} for n in range(1, 9)],
           "trio_payouts": [{"combo": "1-2-3", "payout_yen": 1000}]}
    led = Ledger(paths.ledger_path)
    state = json.loads((live / "state.json").read_text())
    assert _settle(led, {(RK, "result"): res}, state, live, POST + timedelta(hours=1)) == 3
    led.commit()
    assert {(s["time_point"], s["version"], s["stake"], s["returned"]) for s in led.settlements()} == \
        {("1h", "live-aaaaaaaa", 500, 5000)}
    led.close()


def test_backtest_versions_are_not_filtered():
    assert not official.is_live("ml_lgbm", "run-20261101")
    assert official.is_live("text_ai", "mam-v1")


def test_review_comparison_counts_a_race_money_once(tmp_path):
    from keiba.review import comparison
    preds = [{"race_key": RK, "time_point": tp, "method": m, "trio_probs_full": {"1-2-3": 0.2}, "trios": [{"combo": "1-2-3"}]}
             for tp in ("24h", "1h", "15m") for m in ("int_market", "ml_lgbm", "int_market_ml")]
    setts = [{"race_key": RK, "method": m, "wallet": "ev_weighted", "stake": 100, "returned": 300}
             for m in ("int_market", "ml_lgbm", "int_market_ml")]
    assert {x["bet_races"] for x in comparison(preds, setts, {RK: {"actual": "1-2-3"}})} == {1}


def test_http_screens_say_how_many_were_not_counted(tmp_path):
    from keiba.app.server import App, make_handler
    from http.server import ThreadingHTTPServer
    preds = [(RK, "15m", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(minutes=9), 700, 0),
             (RK, "15m", "ml_lgbm", "live-bbbbbbbb", POST + timedelta(minutes=9), 900, 90000)]
    app = App(world(tmp_path, preds), tmp_path / "p.sqlite")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, 0))
    srv.RequestHandlerClass = make_handler(app, srv.server_port)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        for path in ("/results", "/results/methods", "/results/conditions", "/improve", "/improve/history"):
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}{path}", timeout=10) as r:
                body = r.read().decode()
            assert r.status == 200
            if path.startswith("/results"):
                assert "締切＋10分より後に保存した予想 1件は数えていない" in body and "10000%" not in body
    finally:
        srv.shutdown()
        srv.server_close()
        app.store.close()


def test_unknown_post_time_is_not_settled(tmp_path):
    paths = world(tmp_path, [(RK, "15m", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(minutes=9), 700, 0)], posts={}, settle=False)
    led = Ledger(paths.ledger_path)
    res = {"rows": [{"horse_number": n, "finish_position": n, "status": "finished"} for n in range(1, 9)],
           "trio_payouts": [{"combo": "1-2-3", "payout_yen": 1000}]}
    sel = official.load(led.conn, paths.data_dir / "live")
    assert not sel.valid and sel.unknown
    assert _settle(led, {(RK, "result"): res}, {"races": {RK: {"field_size": 8}}}, paths.data_dir / "live", POST + timedelta(hours=1)) == 0
    led.close()


def test_same_version_settles_official_1h_even_if_late_15m_was_settled(tmp_path):
    """期限後の15分前がすでに精算ずみでも、正式な1時間前を精算する（鍵に時点を入れる）。"""
    preds = [(RK, "15m", "ml_lgbm", "live-aaaaaaaa", POST + timedelta(minutes=2), 900, 0)]
    paths = world(tmp_path, preds)
    led = Ledger(paths.ledger_path)
    led._now = lambda: (POST - timedelta(minutes=55)).astimezone(timezone.utc).isoformat(timespec="seconds")
    alloc = {a: {"status": "allocated", "stakes": {"1-2-3": 500}, "total": 500} for a in ALLOC_METHODS}
    led.add_prediction(RK, "1h", "ml_lgbm", "live-aaaaaaaa", {"allocation": alloc}, "i", "m", "c")
    live = paths.data_dir / "live"
    res = {"rows": [{"horse_number": n, "finish_position": n, "status": "finished"} for n in range(1, 9)],
           "trio_payouts": [{"combo": "1-2-3", "payout_yen": 1000}]}
    state = json.loads((live / "state.json").read_text())
    assert _settle(led, {(RK, "result"): res}, state, live, POST + timedelta(hours=1)) == 3
    led.close()


def test_early_version_is_replaced_by_the_first_one_after_material(tmp_path):
    dl = POST - timedelta(minutes=60)
    snaps = [(RK, "1h", "card", dl - timedelta(minutes=12)), (RK, "1h", "odds_trio", dl - timedelta(minutes=11))]
    preds = [(RK, "1h", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(hours=20), 900, 0),
             (RK, "1h", "ml_lgbm", "live-bbbbbbbb", dl - timedelta(minutes=10), 400, 0),
             (RK, "1h", "ml_lgbm", "live-cccccccc", dl, 700, 0)]
    app, wallet, review, src = totals_everywhere(world(tmp_path, preds, snaps=snaps))
    assert app == wallet == review and app[("ml_lgbm", "kelly")][2] == 400
    assert src.excluded["before_material"] == 1


def test_early_only_is_neither_counted_nor_settled(tmp_path):
    preds = [(RK, "1h", "ml_lgbm", "live-aaaaaaaa", POST - timedelta(hours=20), 900, 9000)]
    app, wallet, review, _ = totals_everywhere(world(tmp_path, preds))
    assert app == wallet == review == {}
