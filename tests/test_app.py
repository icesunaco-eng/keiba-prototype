"""確認アプリ：購入記録（追記専用）・精算・画面・サーバーの守り（合成データ。ネットにはつながない）。"""
import json
from pathlib import Path
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from keiba.app import pages
from keiba.app.data import Cache, Source, marks_of
from keiba.app.server import App, make_handler
from keiba.app.store import InputError, Store, clean_stakes, normalize_combo, settle_purchase
from keiba.config import Paths
from keiba.ledger import Ledger

RK = "2026-11-22|京都|11"


# ---------------- 購入記録 ----------------

def test_combo_and_stakes_rules():
    assert normalize_combo("11-3-7") == "3-7-11"
    assert normalize_combo("3,7,11") == "3-7-11"
    for bad in ("3-3-7", "1-2", "0-1-2", "a-b-c", "1-2-19"):
        with pytest.raises(InputError):
            normalize_combo(bad)
    assert clean_stakes([("7-3-11", "1,000"), ("3-11-7", "200"), ("1-2-3", "")]) == {"3-7-11": 1200}
    for bad in ("150", "-100", "abc", "2000000"):
        with pytest.raises(InputError):
            clean_stakes({"1-2-3": bad})


def test_store_is_append_only_and_latest_rev_wins(tmp_path):
    s = Store(tmp_path / "p.sqlite")
    eid = s.add(RK, "buy", {"1-2-3": "1000"}, "最初", "ml_lgbm", "15m")
    s.add(RK, "buy", {"1-2-3": "500", "2-3-4": "300"}, "直した", "ml_lgbm", "15m", entry_id=eid)
    e = s.entry(eid)
    assert e["rev"] == 2 and e["stakes"] == {"1-2-3": 500, "2-3-4": 300} and e["total"] == 800 and e["memo"] == "直した"
    assert [h["rev"] for h in s.history(eid)] == [1, 2]
    with pytest.raises(sqlite3.DatabaseError):
        s.conn.execute("UPDATE purchases SET memo='x'")
    with pytest.raises(sqlite3.DatabaseError):
        s.conn.execute("DELETE FROM purchases")
    s.add(RK, "void", {}, "", entry_id=eid)
    assert s.entries() == [] and s.entry(eid)["kind"] == "void"
    with pytest.raises(InputError):
        s.add(RK, "buy", {}, "")
    with pytest.raises(InputError):
        s.add("2026-01-01|東京|1", "buy", {"1-2-3": "100"}, entry_id=eid)  # 別のレースの記録は直せない
    s.set_note(RK, "メモ1")
    s.set_note(RK, "メモ2")
    assert s.note(RK) == "メモ2"


def _res(fin, finishers=None, scratched=(), payouts=None, field_size=None):
    """全部の行を読めた結果（予定の頭数＝完走＋取消の行数。頭数を変えたいときは field_size）。"""
    from keiba.app.data import make_result
    rows = len(fin) + len(set(scratched))
    return make_result(fin, len(fin) if finishers is None else finishers, set(scratched), payouts or {},
                       rows, field_size or rows)


def test_settle_purchase():
    e = {"kind": "buy", "stakes": {"1-2-3": 1000, "2-3-4": 200}, "total": 1200}
    assert settle_purchase(e, None)["status"] == "pending"
    fin = [(1, 1), (2, 2), (3, 3), (4, 4)]
    st = settle_purchase(e, _res(fin, payouts={"1-2-3": 4560}))
    assert (st["status"], st["stake"], st["returned"], st["hit"], st["counted"]) == ("settled", 1200, 45600, "1-2-3", True)
    assert settle_purchase(e, _res([(5, 1), (6, 2), (7, 3)], payouts={"5-6-7": 9000}))["returned"] == 0
    nopay = settle_purchase(e, _res(fin))                  # 払戻が分からない：0円にしない
    assert nopay["status"] == "unsettled_no_payout" and nopay["counted"] is False
    assert settle_purchase({"kind": "skip", "stakes": {}, "total": 0}, _res(fin))["stake"] == 0


def test_settle_dead_heat_refund_and_void():
    """みにの反例（💚20261120-2）：3着同着・取消返還・不成立。"""
    # 3着同着：3-5-7 と 3-5-9 の両方が的中組。どちらを買っていても払う
    heat = _res([(3, 1), (5, 2), (7, 3), (9, 3)], payouts={"3-5-7": 1380, "3-5-9": 2760})
    assert heat["actual"] is None and heat["label"] == "3-5-7・3-5-9"
    st = settle_purchase({"kind": "buy", "stakes": {"3-5-9": 100, "1-2-3": 100}, "total": 200}, heat)
    assert st["returned"] == 2760 and st["status"] == "settled"
    # 取消返還：8番が取消。8番の入った組 200円は返す。当たり 980円＋返還 200円＝1,180円
    scr = _res([(1, 1), (2, 2), (4, 3)], finishers=10, scratched={8}, payouts={"1-2-4": 980})
    st = settle_purchase({"kind": "buy", "stakes": {"1-2-4": 100, "1-2-8": 200}, "total": 300}, scr)
    assert st["returned"] == 1180 and st["refund"] == 200
    # 不成立：完走2頭。全額返す
    void = _res([(1, 1), (2, 2)], finishers=2)
    st = settle_purchase({"kind": "buy", "stakes": {"1-2-3": 300}, "total": 300}, void)
    assert st["status"] == "refunded_void" and st["returned"] == 300 and st["counted"]


def test_revision_keeps_original_mode_and_ledger_protected(tmp_path, live_paths):
    s = Store(tmp_path / "p.sqlite")
    eid = s.add(RK, "buy", {"1-2-3": 100}, data_mode="live")
    s.add(RK, "buy", {"1-2-3": 200}, data_mode="check", entry_id=eid)    # 検証データを見ながら直しても
    assert [e["data_mode"] for e in s.entries()] == ["live"] and len(s.entries(data_mode="check")) == 0
    with pytest.raises(ValueError):
        Store(live_paths.ledger_path, protect=(live_paths.ledger_path,))
    with pytest.raises(ValueError):    # 名前を変えても、台帳の中身なら使わない
        import shutil
        shutil.copy(live_paths.ledger_path, tmp_path / "x.sqlite")
        Store(tmp_path / "x.sqlite")


def test_concurrent_revisions_do_not_break(tmp_path):
    s = Store(tmp_path / "p.sqlite")
    eid = s.add(RK, "buy", {"1-2-3": 100})
    errs = []

    def work(i):
        try:
            s.add(RK, "buy", {"1-2-3": 100 * (i + 2)}, entry_id=eid)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(s.history(eid)) == 21


# ---------------- 合成の「これからのレース」 ----------------

def _pred(order, conf="A", stakes=None, status="allocated", scratched=(), card_at="2026-11-22T15:15:00+09:00"):
    # card_at：出馬表の取得時刻。発走15:40の15分前の締切（15:25）の収集の窓（15:10〜15:25）の中が既定
    horses = [{"n": n, "p_top3": round(0.9 - i * 0.1, 3)} for i, n in enumerate(order)]
    alloc = {a: {"status": status, "stakes": stakes or {}, "total": sum((stakes or {}).values())}
             for a in ("ev_weighted", "equal_payout", "kelly")}
    return {"race_date": "2026-11-22", "horses": horses, "confidence": conf, "period": "live",
            "trios": [{"combo": "-".join(map(str, sorted(order[:3]))), "p": 0.2}], "allocation": alloc,
            "inputs": {"card_fetched_at": card_at, "scratched_known": list(scratched), "win_odds_ok": True},
            "odds_gate": {"pass": True, "reasons": [], "odds_time": "15:20", "fetched_at": "2026-11-22T15:21:00+09:00"}}


@pytest.fixture
def live_paths(tmp_path):
    paths = Paths(data_dir=tmp_path / "data")
    live = paths.data_dir / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps({"races": {RK: {
        "race_date": "2026-11-22", "venue": "京都", "race_number": 11, "post_time": "15:40", "surface": "芝",
        "distance_m": 1600, "field_size": 8, "race_name": "マイルチャンピオンS 3歳以上オープン"}}}, ensure_ascii=False))
    led = Ledger(paths.ledger_path)
    v = "live-test0001"
    led.add_prediction(RK, "1h", "ml_lgbm", v, _pred([3, 7, 9, 1, 2], "B", {"3-7-9": 600}, card_at="2026-11-22T14:30:00+09:00"),
                       "i", "m", "c")
    led.add_prediction(RK, "15m", "ml_lgbm", v, _pred([3, 7, 11, 1, 2], "A", {"3-7-11": 1200, "2-3-7": 800}, scratched=[5]), "i", "m", "c")
    led.add_prediction(RK, "15m", "rule", v, {**_pred([7, 3, 2, 1, 11], "C"), "allocation": {"status": "not_allocated_role_a"}}, "i", "m", "c")
    led.add_prediction(RK, "15m", "int_market_ml", v, {**_pred([3, 11, 7, 1, 2]), "horses": [{"n": n} for n in (3, 11, 7, 1, 2)],
                                                       "trio_probs_full": {"3-7-11": 0.5, "1-3-11": 0.3, "2-3-7": 0.2}}, "i", "m", "c")
    led.add_settlement(RK, "15m", "ml_lgbm", v, "ev_weighted", {"status": "settled", "stake": 2000, "returned": 5000, "hits": ["3-7-11"]})
    led.commit()
    led.close()
    return paths


def test_source_reads_live_ledger(live_paths):
    src = Source(live_paths, "live")
    assert src.days() == ["2026-11-22"] and src.latest_tp(RK) == "15m"
    m, p = src.pick(RK, "ml", "15m")
    assert m == "ml_lgbm" and [n for _, n in marks_of(p)] == [3, 7, 11]
    m, p = src.pick(RK, "int", "15m")  # 統合は組の確率から馬の確率を足し上げる
    assert m == "int_market_ml" and [n for _, n in marks_of(p)][0] == 3
    assert src.pick(RK, "mam", "15m") == (None, None)
    assert src.version == "live-test0001"


def test_cache_reloads_when_ledger_changes(live_paths):
    c = Cache(live_paths)
    a = c.get("live")
    assert c.get("live") is a
    led = Ledger(live_paths.ledger_path)
    led.add_prediction(RK, "24h", "ml_lgbm", "live-test0001", _pred([1, 2, 3]), "i", "m", "c")
    led.commit()
    led.close()
    assert c.get("live") is not a


def test_pages_render_and_escape(live_paths, tmp_path):
    src = Source(live_paths, "live")
    store = Store(tmp_path / "p.sqlite")
    ctx = {"mode": "live", "theme": "auto", "size": "normal", "token": "t"}
    store.set_note(RK, "<script>alert(1)</script>")
    q = {"rk": RK, "tp": "15m"}
    out = {
        "races": pages.races_page(ctx, src, store, {"day": "2026-11-22"}),
        "race": pages.race_page(ctx, src, store, q),
        "horses": pages.horses_page(ctx, src, store, q),
        "compare": pages.compare_page(ctx, src, store, q),
        "timeline": pages.timeline_page(ctx, src, store, {"rk": RK}),
        "history": pages.history_detail_page(ctx, src, store, {"rk": RK}),
        "results": pages.results_page(ctx, src, store, {}),
        "methods": pages.results_methods_page(ctx, src, store, {}),
        "improve": pages.improve_page(ctx, src, store, {}),
    }
    assert "マイルチャンピオンS" in out["races"] and "15分前" in out["races"]
    assert "3-7-11" in out["race"] and "¥2,000" in out["race"] and "取消・除外：5番" in out["race"]
    assert "事実" in out["race"] and "推論" in out["race"]
    assert "★が 9番 → 11番" in out["timeline"] and "自信度 B → A" in out["timeline"]
    assert "<script>alert(1)</script>" not in out["history"] and "&lt;script&gt;" in out["history"]
    assert "250%" in out["results"]  # 2,000円 → 5,000円
    assert "運用中" in out["improve"]
    for html in out.values():  # 1画面のカードは多すぎない
        assert html.count('class="card') <= 30


def test_empty_data_shows_zero_not_numbers(tmp_path):
    paths = Paths(data_dir=tmp_path / "data")
    src = Source(paths, "live")
    store = Store(tmp_path / "p.sqlite")
    ctx = {"mode": "live", "theme": "auto", "size": "normal", "token": "t"}
    assert "まだレースが無いよ" in pages.races_page(ctx, src, store, {})
    r = pages.results_page(ctx, src, store, {})
    assert "—" in r and "0R" in r
    assert "まだ0件だよ" in pages.history_page(ctx, src, store, {})


# ---------------- サーバーの守り ----------------

@pytest.fixture
def server(live_paths, tmp_path):
    app = App(live_paths, tmp_path / "p.sqlite")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield app, port
    httpd.shutdown()
    httpd.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _req(port, path, data=None, host=None, origin=None):
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if origin:
        headers["Origin"] = origin
    body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers)
    try:
        r = urllib.request.build_opener(NoRedirect).open(req)
        return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def test_server_pages_and_guards(server):
    app, port = server
    for path in ("/races", "/race?rk=" + urllib.parse.quote(RK), "/history", "/results", "/improve", "/menu",
                 "/status", "/settings", "/about", "/purchases", "/improve/compare", "/results/skips"):
        code, body, headers = _req(port, path)
        assert code == 200, path
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert _req(port, "/races", host="evil.example:80")[0] == 403          # よその名前で来たら断る
    data = {"token": "wrong", "rk": RK, "kind": "buy", "combo": ["1-2-3"], "yen": ["100"]}
    assert _req(port, "/buy", data)[0] == 403                               # 合言葉が違う
    data["token"] = app.token
    assert _req(port, "/buy", data, origin="http://evil.example")[0] == 403  # よそのページからの送信
    assert app.store.entries() == []


def test_server_buy_flow(server):
    app, port = server
    ok = {"token": app.token, "rk": RK, "kind": "buy", "m": "ml_lgbm", "tp": "15m",
          "combo": ["11-7-3", "2-3-7", ""], "yen": ["1200", "800", ""], "memo": "直前に決めた"}
    code, body, _ = _req(port, "/buy", {**ok, "yen": ["1200", "150", ""]}, origin=f"http://127.0.0.1:{port}")
    assert code == 400 and "100円単位" in body and "11-7-3" in body       # 入力は残して、直す所を伝える
    code, _, headers = _req(port, "/buy", ok, origin=f"http://127.0.0.1:{port}")
    assert code == 303 and "/history/detail" in headers["Location"]
    (e,) = app.store.entries()
    assert e["stakes"] == {"2-3-7": 800, "3-7-11": 1200} and e["data_mode"] == "live"
    code, csv_text, _ = _req(port, "/purchases.csv")
    assert code == 200 and "3-7-11" in csv_text
    code, _, _ = _req(port, "/buy/void", {"token": app.token, "entry": e["entry_id"]})
    assert code == 303 and app.store.entries() == []
    code, _, headers = _req(port, "/settings", {"token": app.token, "mode": "check", "theme": "dark", "size": "bogus"})
    assert code == 303 and "mode=check" in headers.get("Set-Cookie", "")


# ---------------- 追加：チェック・新旧くらべ・合言葉・通知 ----------------

from datetime import datetime, timedelta  # noqa: E402

from keiba.app import checks  # noqa: E402
from keiba.app.server import notify_loop  # noqa: E402


def test_pre_publish_check_finds_problems():
    p = _pred([3, 7, 11, 1, 2], scratched=[5])
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    ok = checks.pre_publish_check(p, {"3-7-11": 1200, "2-3-7": 800}, race)
    assert checks.check_summary(ok)[1] == 0
    bad = checks.pre_publish_check(p, {"3-5-7": 1200, "2-3-7": 150, "3-7-99": 100}, race)
    labels = {i["label"]: i for i in bad if i["ok"] is False}
    assert "5番" in labels["取消・除外の馬が買い目に無い"]["detail"]
    assert "99" in labels["組の形（3頭・出走している馬番）"]["detail"]
    assert "100円単位・1レース上限 ¥10,000" in labels
    many = {f"1-2-{n}": 100 for n in range(3, 15)}
    assert any(i["ok"] is False and "点数" in i["label"] for i in checks.pre_publish_check(p, many, race))
    gate_ng = {**p, "odds_gate": {"pass": False, "reasons": ["古いオッズ（発表 15:00・取得 15:21）"]}}
    assert any(i["ok"] is False and "オッズ" in i["label"] for i in checks.pre_publish_check(gate_ng, {}, race))


def test_bias_conditions_comment_and_flags():
    p = _pred([3, 7, 11, 1, 2])
    bias = checks.bias_check(p, {"3-7-11": 900, "2-3-7": 100})
    assert any("3番" in x and "全部はずれ" in x for _, x in bias) and any("90%" in x for _, x in bias)
    assert any("◎" in x for _, x in checks.bias_check(p, {"1-2-7": 100, "2-7-11": 100}))
    cond = checks.change_conditions(p, "1h")
    assert any("★ 11番と4番手 1番" in x for _, x in cond) and any("15分前" in x for _, x in cond)
    assert checks.horse_comment({"coverage": 0.9, "groups": {"distance": 9, "course": 8, "recent": 2, "pace": None}}) == "距離◎ ／ コース◎ ／ 近走△"
    assert checks.horse_comment({"coverage": 0.25, "groups": {"draw": 5}}) == "目立つ項目なし（データ少なめ）"
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    after = datetime(2026, 11, 22, 16, 0, tzinfo=checks.JST)
    flags = [t for _, t in checks.card_flags(_pred([3, 7, 11], scratched=[5]), {"3-5-7": 100}, race, False, after)]
    assert flags[:3] == ["発走済み", "取消あり", "チェック×"]


def test_new_pages_render(live_paths, tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "now_jst", lambda: datetime(2026, 11, 22, 10, 0, tzinfo=checks.JST))
    src = Source(live_paths, "live")
    store = Store(tmp_path / "p.sqlite")
    ctx = {"mode": "live", "theme": "auto", "size": "normal", "token": "t", "refresh": "on"}
    race = pages.race_page(ctx, src, store, {"rk": RK, "tp": "15m"})
    assert "確認できない 2件" in race   # 最新の出馬表で取消・全頭の馬番を照合できていない
    src.scratched_now[RK] = {5}
    src.card_checked_at[RK] = "2026-11-22T15:00:00+09:00"
    race = pages.race_page(ctx, src, store, {"rk": RK, "tp": "15m"})
    assert "チェック ✓" in race and "見送る（1タップ）" in race and 'http-equiv="refresh"' in race
    chk = pages.check_page(ctx, src, store, {"rk": RK, "tp": "15m"})
    assert "公開前チェック" in chk and "買い目の偏り" in chk and "予想が変わる条件" in chk
    assert chk.count('class="card') <= 4
    off = pages.races_page({**ctx, "refresh": "off"}, src, store, {"day": "2026-11-22"})
    assert 'http-equiv="refresh"' not in off and "自信度A 1" in off
    form = pages.buy_form(ctx, src, store, {"rk": RK, "m": "ml_lgbm", "tp": "15m"})
    assert 'http-equiv="refresh"' not in form          # 入力の画面は読み直さない
    stale = pages.races_page({**ctx, "health": {"checked_at": "2026-11-01T10:00:00+09:00"}}, src, store, {"day": "2026-11-22"})
    assert "止まっているかも" in stale
    fresh = pages.races_page({**ctx, "health": {"checked_at": "2026-11-22T09:55:00+09:00"}}, src, store, {"day": "2026-11-22"})
    assert "止まっているかも" not in fresh
    store.set_note("improve|a|b", "採用したい")
    assert "improve|a|b" not in store.noted_races()


def test_horse_comments_use_rule_groups(live_paths, tmp_path):
    led = Ledger(live_paths.ledger_path)
    rule = {**_pred([7, 3, 2, 1, 11], "C"), "allocation": {"status": "not_allocated_role_a"}}
    rule["horses"] = [{"n": 3, "p_top3": 0.6, "coverage": 1, "score": 7, "groups": {"distance": 9, "recent": 2}},
                      {"n": 7, "p_top3": 0.7, "coverage": 1, "score": 8, "groups": {"course": 8}}]
    led.add_prediction(RK, "1h", "rule", "live-test0001", rule, "i", "m", "c")
    led.commit()
    led.close()
    src = Source(live_paths, "live")
    out = pages.horses_page({"mode": "live", "token": "t"}, src, Store(tmp_path / "p.sqlite"), {"rk": RK, "tp": "1h"})
    assert "距離◎ ／ 近走△" in out and "推論" in out


def _versions_ledger(paths):
    led = Ledger(paths.ledger_path)
    for v, hit in (("v0.1+aaaa", False), ("v0.1+bbbb", True)):
        led.add_prediction(RK, "1h", "ml_lgbm", v, _pred([3, 7, 11]), "i", "m", "c")
        led.add_settlement(RK, "1h", "ml_lgbm", v, "ref_top10_equal",
                           {"status": "settled", "stake": 1000, "returned": 3000 if hit else 0, "hits": ["3-7-11"] if hit else []})
    led.commit()
    led.close()


def test_versions_compare_and_decision(server, live_paths):
    app, port = server
    _versions_ledger(live_paths)
    code, body, _ = _req(port, "/improve/versions")
    assert code == 200 and "0%→300%" in body and "+300.0pt" in body
    data = {"token": app.token, "a": "v0.1+aaaa", "b": "v0.1+bbbb", "decision": "adopt", "memo": "回収率が上がった"}
    code, _, headers = _req(port, "/improve/decision", data)
    assert code == 303
    assert app.store.note("improve|v0.1+aaaa|v0.1+bbbb").startswith("採用したい")
    assert _req(port, "/improve/decision", {**data, "decision": "bogus"})[0] == 400


def test_one_tap_skip_returns_to_race(server):
    app, port = server
    data = {"token": app.token, "rk": RK, "kind": "skip", "m": "ml_lgbm", "tp": "15m", "back": "race", "tab": "ml"}
    code, _, headers = _req(port, "/buy", data)
    loc = headers["Location"]
    assert code == 303 and loc.startswith("/race?") and "saved=skip" in loc and app.store.entries()[0]["kind"] == "skip"
    code, _, headers = _req(port, "/buy", {**data, "tab": "//evil.example", "tp": "x", "next": "//evil.example/race?x"})
    assert headers["Location"].startswith("/race?rk=") and "evil" not in headers["Location"]   # よそへは飛ばさない
    code, body, _ = _req(port, loc)
    assert "見送りを記録したよ" in body


def test_lan_login_required_from_other_devices(live_paths, tmp_path):
    app = App(live_paths, tmp_path / "p.sqlite", passphrase="kusamochi-2026")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    handler = make_handler(app, port, [f"192.168.1.20:{port}"])
    handler._local = lambda self: False      # よその機械から来たことにする
    httpd.RequestHandlerClass = handler
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        host = f"192.168.1.20:{port}"
        code, body, _ = _req(port, "/races", host=host)
        assert code == 401 and "合言葉" in body and "レース</h1>" not in body
        assert _req(port, "/buy", {"token": app.token, "rk": RK, "kind": "skip"}, host=host)[0] == 303  # ログインへ
        assert app.store.entries() == []
        code, body, _ = _req(port, "/login", {"passphrase": "wrong"}, host=host)
        assert code == 401
        code, _, headers = _req(port, "/login", {"passphrase": "kusamochi-2026"}, host=host)
        assert code == 303 and "auth=" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
        cookie = headers["Set-Cookie"].split(";")[0]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/races", headers={"Host": host, "Cookie": cookie})
        assert urllib.request.urlopen(req).status == 200
        for _ in range(5):
            _req(port, "/login", {"passphrase": "nope"}, host="192.168.1.20:%d" % port)
        assert _req(port, "/login", {"passphrase": "kusamochi-2026"}, host=host)[0] == 429   # 続けてまちがえたら止める
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_notify_loop_sends_once(live_paths, tmp_path, monkeypatch):
    app = App(live_paths, tmp_path / "p.sqlite")
    post = checks.post_datetime(app.cache.get("live").races[RK])
    monkeypatch.setattr(checks, "now_jst", lambda: post - timedelta(minutes=10))
    sent = []
    stop = threading.Event()

    def sender(title, text):
        sent.append((title, text))
        if len(sent) >= 1:
            stop.set()
    notify_loop(app, stop, interval=0, sender=sender)
    assert sent and sent[0][0].startswith("京都11R") and "自信度A" in sent[0][1]


# ---------------- みにの独立検証（💚20261120-2）の反例 ----------------

def test_check_does_not_pass_without_material():
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    p = _pred([3, 7, 11, 1, 2])
    bare = {**p, "horses": [{"n": None}], "inputs": {"card_fetched_at": "2026-11-22T14:30:00+09:00"}}
    items = {i["label"]: i for i in checks.pre_publish_check(bare, {"3-7-11": 100}, race)}
    assert items["取消・除外の馬が買い目に無い"]["ok"] is None          # 取消情報が無い → 判定できない
    assert items["組の形（3頭・出走している馬番）"]["ok"] is None       # 出走表が無い → 照合できない
    late = {**p, "inputs": {**p["inputs"], "card_fetched_at": "2026-11-22T16:10:00+09:00"}}
    fresh = {i["label"]: i for i in checks.pre_publish_check(late, {"3-7-11": 100}, race)}["出馬表の新しさ"]
    assert fresh["ok"] is False and "発走の" in fresh["detail"]          # 発走後の取得は×
    naive = {**p, "inputs": {**p["inputs"], "card_fetched_at": "2026-11-22T14:30:00"}}
    assert {i["label"]: i for i in checks.pre_publish_check(naive, {"3-7-11": 100}, race)}["出馬表の新しさ"]["ok"] is True
    four = {i["label"]: i for i in checks.pre_publish_check(p, {"1-1-3-7": 100}, race)}
    assert four["組の形（3頭・出走している馬番）"]["ok"] is False        # 4つ書いた組は通さない


def test_check_uses_scratches_after_prediction():
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    p = _pred([3, 7, 11, 1, 2])                        # 予想のときは取消なし
    item = {i["label"]: i for i in checks.pre_publish_check(p, {"3-7-11": 100}, race, scratched_now={11})}
    assert item["取消・除外の馬が買い目に無い"]["ok"] is False and "予想の後に取消" in item["取消・除外の馬が買い目に無い"]["detail"]
    assert ("warn", "取消あり") in checks.card_flags(p, {"3-7-11": 100}, race, False, scratched_now={11})
    z = [t for _, t in checks.change_conditions({**p, "confidence": "Z"}, "15m")]
    assert any("材料が足りず" in t for t in z) and not any("割れやすいレース" in t for t in z)


def test_recent_order_counts_and_months(live_paths, tmp_path):
    src = Cache(live_paths).get("live")
    for n in (9, 12):
        src.races[f"2026-11-22|東京|{n}"] = {"race_key": f"2026-11-22|東京|{n}", "race_date": "2026-11-22",
                                            "venue": "東京", "race_number": n, "post_time": None}
    keys = {"2026-11-22|東京|9", "2026-11-22|東京|12"}
    assert pages._recent_keys(src, keys, "50") == keys
    assert pages._recent_keys(src, keys, "1") == {"2026-11-22|東京|12"}      # 12R の方が新しい
    store = Store(tmp_path / "p.sqlite")
    src.results[RK] = _res([(3, 1), (7, 2), (11, 3)], payouts={"3-7-11": 500})
    store.add(RK, "buy", {"3-7-11": 100})
    store.add(RK, "buy", {"1-2-3": 100})
    real = pages._real(src, store)
    assert real["races"] == 1 and real["entries"] == 2 and real["stake"] == 200 and real["returned"] == 500
    src.results.pop(RK)
    src.results[RK] = _res([(3, 1), (7, 2), (11, 3)])   # 払戻が分からない → 回収率に入れない
    real = pages._real(src, store)
    assert real["unsettled"] == 2 and real["stake"] == 0 and real["roi"] is None
    # 月別は12か月で切らない
    src.settlements = [{"race_key": f"20{24 + i // 12}-{i % 12 + 1:02d}-01|東京|1", "time_point": "1h", "method": "ml_lgbm",
                        "wallet": "ev_weighted", "status": "settled", "stake": 100, "returned": 0, "hits": []} for i in range(15)]
    out = pages.results_conditions_page({"token": "t", "mode": "live"}, src, store, {"n": "all", "d": "month"})
    assert out.count("<tr>") == 16


def test_unsettled_wallet_is_not_zero_yen(live_paths):
    src = Cache(live_paths).get("live")
    src.settlements = [{"race_key": RK, "time_point": "15m", "method": "ml_lgbm", "wallet": "ev_weighted", "status": "settled",
                        "stake": 1000, "returned": 2200, "hits": ["3-7-11"]},
                       {"race_key": "2026-11-22|京都|10", "time_point": "15m", "method": "ml_lgbm", "wallet": "ev_weighted",
                        "status": "unsettled_no_payout", "stake": 1000, "returned": 0, "hits": []}]
    row = pages.wallet_table(src, "ev_weighted")[0]
    assert row["roi"] == 2.2 and row["pending"] == 1


def test_amount_only_change_is_reported():
    a = _pred([3, 7, 11, 1, 2], stakes={"3-7-11": 600})
    b = _pred([3, 7, 11, 1, 2], stakes={"3-7-11": 1200})
    lines = pages._change_lines(a, b, "ev_weighted")
    assert any("金額だけ変わった" in x for x in lines)


def test_csv_cells_are_not_formulas(live_paths, tmp_path):
    src = Cache(live_paths).get("live")
    store = Store(tmp_path / "p.sqlite")
    store.add(RK, "buy", {"3-7-11": 100}, memo="=HYPERLINK(\"http://x\")")
    out = pages.purchases_csv(src, store)
    assert "'=HYPERLINK" in out and ",=HYPERLINK" not in out


def test_check_mode_records_are_not_called_real(live_paths, tmp_path):
    src = Cache(live_paths).get("check")
    out = pages.results_page({"token": "t", "mode": "check"}, src, Store(tmp_path / "p.sqlite"), {})
    assert "練習の記録" in out and "実購入の回収率" not in out


def test_app_id_and_login_lock_is_atomic(server, live_paths, tmp_path):
    app, port = server
    assert _req(port, "/app-id")[1] == "keiba-confirm-app"
    lan = App(live_paths, tmp_path / "q.sqlite", passphrase="kusamochi-2026")
    res = []
    ts = [threading.Thread(target=lambda: res.append(lan.try_login("10.0.0.9", "wrong"))) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res.count("ng") == 5 and res.count("locked") == 25
    assert lan.try_login("10.0.0.9", "kusamochi-2026") == "locked"


def test_races_filter_soon_and_warn(live_paths, tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(pages, "now_jst", lambda: datetime(2026, 11, 22, 15, 0, tzinfo=checks.JST))
    src = Cache(live_paths).get("live")
    store = Store(tmp_path / "p.sqlite")
    ctx = {"token": "t", "mode": "live"}
    out = pages.races_page(ctx, src, store, {"day": "2026-11-22", "f": "soon"})
    assert "発走60分以内 1" in out and "京都11R" in out.replace(" ", "")
    monkeypatch.setattr(pages, "now_jst", lambda: datetime(2026, 11, 22, 9, 0, tzinfo=checks.JST))
    out = pages.races_page(ctx, src, store, {"day": "2026-11-22", "f": "soon"})
    assert "あてはまるレースは無いよ" in out


def test_history_two_weeks_and_compare_reasons(live_paths, tmp_path, monkeypatch):
    src = Cache(live_paths).get("live")
    store = Store(tmp_path / "p.sqlite")
    out = pages.history_page({"token": "t", "mode": "live"}, src, store, {"f": "2w"})
    assert "2週間" in out
    out = pages.compare_page({"token": "t", "mode": "live"}, src, store, {"rk": RK, "tp": "15m"})
    assert "組：" in out


# ---------------- みにの再検証（💚20261120-4）の反例 ----------------

def test_dead_heat_with_missing_payout_waits():
    heat = _res([(3, 1), (5, 2), (7, 3), (9, 3)], payouts={"3-5-7": 1380})       # 3-5-9 の払戻が欠けている
    assert heat["winners"] == {"3-5-7", "3-5-9"} and heat["payouts_missing"] == ["3-5-9"]
    st = settle_purchase({"kind": "buy", "stakes": {"3-5-9": 100}, "total": 100}, heat)
    assert st["status"] == "unsettled_no_payout" and not st["counted"]
    ok = settle_purchase({"kind": "buy", "stakes": {"3-5-7": 100}, "total": 100}, heat)
    assert ok["status"] == "settled" and ok["returned"] == 1380
    three = _res([(1, 1), (2, 3), (4, 3), (6, 3)], payouts={"1-2-4": 900})
    assert three["winners"] == {"1-2-4", "1-2-6", "1-4-6"}
    assert settle_purchase({"kind": "buy", "stakes": {"1-4-6": 100}, "total": 100}, three)["status"] == "unsettled_no_payout"


def test_unreadable_result_is_not_void(live_paths):
    src = Cache(live_paths).get("live")
    assert RK not in src.results          # 結果の原本が無い・読めないときは結果待ち
    st = settle_purchase({"kind": "buy", "stakes": {"1-2-3": 300}, "total": 300}, src.results.get(RK))
    assert st["status"] == "pending" and st["returned"] == 0


def test_scratch_check_needs_latest_card():
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    p = _pred([3, 7, 11, 1, 2], scratched=[5])      # 予想のときの取消情報だけ
    item = {i["label"]: i for i in checks.pre_publish_check(p, {"3-7-11": 100}, race)}["取消・除外の馬が買い目に無い"]
    assert item["ok"] is None and "照合できていない" in item["detail"]
    item = {i["label"]: i for i in checks.pre_publish_check(p, {"3-7-11": 100}, race, scratched_now=set(),
                                                             checked_at="2026-11-22T15:10:00+09:00")}["取消・除外の馬が買い目に無い"]
    assert item["ok"] is True and "11/22 15:10" in item["detail"]
    flags = checks.card_flags(p, {"3-7-11": 100}, race, False)
    assert ("warn", "確認できない項目あり") in flags


def test_early_prediction_is_labeled():
    race = {"race_date": "2026-11-21", "post_time": "10:30"}
    p = {**_pred([3, 7, 11, 1, 2]), "inputs": {"card_fetched_at": "2026-11-20T10:15:00+09:00", "scratched_known": []}}
    assert checks.early_prediction(p, race, "1h")
    item = {i["label"]: i for i in checks.pre_publish_check(p, {"3-7-11": 100}, race, tp="1h")}["時点の材料"]
    assert item["ok"] is False
    assert ("warn", "早めの参考予想") in checks.card_flags(p, {}, race, False, tp="1h")
    fresh = {**p, "inputs": {"card_fetched_at": "2026-11-21T09:20:00+09:00"}}
    assert checks.early_prediction(fresh, race, "1h") is None


def test_versions_compare_skips_unsettled_side(live_paths):
    from keiba.app.data import version_compare
    led = Ledger(live_paths.ledger_path)
    for rk, a, b in ((RK, ("settled", 100, 200), ("settled", 100, 200)),
                     ("2026-11-22|京都|10", ("unsettled_no_payout", 100, 0), ("settled", 100, 350))):
        for v, (st, stake, ret) in (("v1", a), ("v2", b)):
            led.add_settlement(rk, "1h", "ml_lgbm", v, "ref_top10_equal", {"status": st, "stake": stake, "returned": ret, "hits": []})
    led.commit()
    led.close()
    row = version_compare(live_paths.ledger_path, "v1", "v2")["rows"][0]
    assert row["races"] == 1 and row["a"]["roi"] == 2.0 and row["b"]["roi"] == 2.0


def test_settlement_follows_shown_version(live_paths):
    src = Cache(live_paths).get("live")
    src.versions = [{"version": "live-old"}, {"version": "live-new"}]
    src.preds[RK][("ml_lgbm", "15m")] = {**src.preds[RK][("ml_lgbm", "15m")], "version": "live-new"}
    src.settlements = [
        {"race_key": RK, "time_point": "15m", "method": "ml_lgbm", "version": "live-new", "wallet": "ev_weighted",
         "status": "settled", "stake": 100, "returned": 220, "hits": ["3-7-11"]},
        {"race_key": RK, "time_point": "15m", "method": "ml_lgbm", "version": "live-old", "wallet": "ev_weighted",
         "status": "unsettled_no_payout", "stake": 100, "returned": 0, "hits": []}]
    row = pages.wallet_table(src, "ev_weighted")[0]
    assert (row["races"], row["roi"], row["pending"]) == (1, 2.2, 0)


def test_old_mode_moves_are_read_as_first_mode(tmp_path):
    path = tmp_path / "p.sqlite"
    s = Store(path)
    eid = s.add(RK, "buy", {"1-2-3": 100}, data_mode="live")
    s.conn.execute("INSERT INTO purchases (entry_id,rev,created_at,race_key,method,time_point,kind,stakes,memo,data_mode)"
                   " VALUES (?,2,'x',?,NULL,NULL,'buy','{\"1-2-3\": 200}','','check')", (eid, RK))   # 旧版で移った履歴
    s.conn.commit()
    e = s.entries()[0]
    assert e["data_mode"] == "live" and e["mode_mixed"] and len(s.entries(data_mode="live")) == 1
    s.add(RK, "buy", {"1-2-3": 300}, data_mode="check", entry_id=eid)
    assert s.conn.execute("SELECT data_mode FROM purchases WHERE entry_id=? AND rev=3", (eid,)).fetchone()[0] == "live"


def test_two_store_instances_revise_together(tmp_path):
    path = tmp_path / "p.sqlite"
    a, b = Store(path), Store(path)
    eid = a.add(RK, "buy", {"1-2-3": 100})
    errs = []

    def work(st, yen):
        try:
            st.add(RK, "buy", {"1-2-3": yen}, entry_id=eid)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)
    ts = [threading.Thread(target=work, args=(st, 100 * (i + 2))) for i, st in enumerate([a, b] * 5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(a.history(eid)) == 11


def test_store_path_with_symbols_makes_no_extra_file(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    p = d / "p?a#b.sqlite"
    Store(p).close()
    Store(p).close()
    assert sorted(x.name for x in d.iterdir()) == ["p?a#b.sqlite"]


def test_compare_groups_and_ranked_horses(live_paths, tmp_path):
    src = Cache(live_paths).get("live")
    store = Store(tmp_path / "p.sqlite")
    src.preds[RK] = {k: v for k, v in src.preds[RK].items() if k[0] == "ml_lgbm"}
    out = pages.compare_page({"token": "t", "mode": "live"}, src, store, {"rk": RK, "tp": "15m"})
    assert "1つだけ" in out and "割れた" not in out
    src.preds[RK][("text_ai", "15m")] = {"horses": [{"n": 5, "rank": 2}, {"n": 3, "rank": 1}, {"n": 8, "rank": None}],
                                         "trios": [], "confidence": None}
    out = pages.horses_page({"token": "t", "mode": "live"}, src, store, {"rk": RK, "m": "mam", "tp": "15m"})
    assert "1位" in out and "2位" in out and "順位だけ" in out and "較正した推定" not in out


def test_two_weeks_lists_races_without_predictions(live_paths, tmp_path, monkeypatch):
    import datetime as dt

    class D(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 11, 23)
    monkeypatch.setattr(pages, "date", D)
    src = Cache(live_paths).get("live")
    src.races["2026-11-21|東京|1"] = {"race_key": "2026-11-21|東京|1", "race_date": "2026-11-21", "venue": "東京",
                                     "race_number": 1, "post_time": "10:00"}
    out = pages.history_page({"token": "t", "mode": "live"}, src, Store(tmp_path / "p.sqlite"), {"f": "2w"})
    assert "東京1R" in out.replace(" ", "")


def test_notify_once_when_scratch_and_a_overlap(live_paths, tmp_path, monkeypatch):
    from datetime import datetime
    from keiba.app import server as srv
    app = App(live_paths, tmp_path / "p.sqlite")
    monkeypatch.setattr(checks, "now_jst", lambda: datetime(2026, 11, 22, 15, 30, tzinfo=checks.JST))
    src = app.cache.get("live")
    src.scratched_now[RK] = {11}
    sent = []
    stop = threading.Event()
    t = threading.Thread(target=srv.notify_loop, args=(app, stop, 0.05, 20, lambda a, b: sent.append(b)), daemon=True)
    t.start()
    import time
    time.sleep(0.4)
    stop.set()
    t.join(2)
    assert len(sent) == 1 and "取消" in sent[0] and "自信度A" in sent[0]


def test_card_readability_rule():
    from keiba.app.data import card_problem
    ok = [{"horse_number": 1, "scratch": None}, {"horse_number": 2, "scratch": "取消"}]
    assert card_problem(ok) is None and card_problem(ok, 2) is None
    assert card_problem([{"horse_number": 1}])                                   # 取消の欄が無い
    assert card_problem([{"horse_number": 1, "scratch": "?"}])                   # 決まっていない値
    assert card_problem([{"horse_number": None, "scratch": None}])               # 馬番を読めない
    assert card_problem([])


# ---------------- 再々検証（💚20261120-5）の反例 ----------------

def _card(nums):
    return [{"horse_number": n, "scratch": None} for n in nums]


def test_card_missing_or_duplicate_rows_are_not_checked():
    from keiba.app.data import card_problem
    assert "3番" in card_problem(_card([1, 2, 4, 5, 6]), 6)          # 6頭中3番の行が無い
    assert "重複" in card_problem(_card([1, 2, 2, 4, 5, 6]), 6)      # 行数は6でも馬番2が重複
    assert "4番" in card_problem(_card([1, 2, 3]), 4)                # 予定4頭で3頭しか読めない
    assert "3番" in card_problem(_card([1, 2, 4]))                   # 頭数が分からなくても、番号の欠けは分かる
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    p = _pred([1, 2, 3, 4, 5, 6])
    note = card_problem(_card([1, 2, 4, 5, 6]), 6)
    items = {i["label"]: i for i in checks.pre_publish_check(p, {"1-2-3": 100}, race, None, None, None, None, note)}
    assert items["取消・除外の馬が買い目に無い"]["ok"] is None and "3番" in items["取消・除外の馬が買い目に無い"]["detail"]
    assert items["組の形（3頭・出走している馬番）"]["ok"] is None
    flags = checks.card_flags(p, {"1-2-3": 100}, race, False, None, None, None, None, None, note)
    assert ("warn", "確認できない項目あり") in flags


def test_source_skips_partial_card(live_paths, monkeypatch):
    from keiba.app import data as d
    monkeypatch.setattr(d, "card_problem", lambda card, size: "8頭のうち 3番 の行が無い")
    src = Source(live_paths, "live")
    assert RK not in src.scratched_now      # 原本が無い／欠けていれば「取消なし」にしない


def test_partial_result_is_held_not_refunded():
    from keiba.app.data import make_result
    from keiba.app.store import settle_purchase
    res = make_result([(1, 1), (2, 2)], 2, set(), {"1-2-3": 500}, rows=2, field_size=4)
    assert res["complete"] is False
    st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-3": 100}}, res)
    assert st["status"] == "partial_result" and not st["counted"]       # 全額返還済みにはしない
    whole = make_result([(1, 1), (2, 2)], 2, set(), {}, rows=2, field_size=2)
    assert whole["complete"] is True                                      # 全部読めて完走2頭なら不成立
    assert settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-3": 100}}, whole)["status"] == "refunded_void"
    top = make_result([(1, 1), (2, 2), (3, None), (4, None)], 4, set(), {}, rows=4, field_size=4)
    assert top["complete"] is False                                       # 完走4頭なのに3着が読めない


def test_missing_tie_payout_keeps_sure_refund():
    from keiba.app.data import make_result
    from keiba.app.store import settle_purchase
    res = make_result([(1, 1), (2, 2), (4, 3), (6, 3)], 4, {5}, {"1-2-6": 900}, rows=6, field_size=6)
    assert res["payouts_missing"] == ["1-2-4"]
    st = settle_purchase({"kind": "buy", "total": 400, "stakes": {"1-2-4": 200, "1-2-5": 200}}, res)
    assert st["status"] == "unsettled_no_payout" and st["refund"] == 200 and not st["counted"]


def test_material_timing_window():
    race = {"race_date": "2026-11-22", "post_time": "15:40"}

    def t(at):
        return checks.material_timing({"inputs": {"card_fetched_at": at}}, race, "1h")[0]
    assert t("2026-11-22T14:25:00+09:00") == "ok"            # 締切14:40の15分前（窓の始まり）
    assert t("2026-11-22T14:40:00+09:00") == "ok"
    assert t("2026-11-22T14:00:00+09:00") == "early"         # 発走100分前：窓より前は早めの参考
    assert t("2026-11-22T15:10:00+09:00") == "late"          # 締切の30分後
    assert t("not-a-time") == "unknown"
    p = {**_pred([1, 2, 3]), "inputs": {"card_fetched_at": "2026-11-22T15:10:00+09:00"}}
    item = {i["label"]: i for i in checks.pre_publish_check(p, {}, race, tp="1h")}["時点の材料"]
    assert item["ok"] is False and "締切" in item["detail"]
    broken = {**p, "inputs": {"card_fetched_at": "??"}}
    assert {i["label"]: i for i in checks.pre_publish_check(broken, {}, race, tp="1h")}["時点の材料"]["ok"] is None
    assert ("warn", "締切後の材料") in checks.card_flags(p, {}, race, False, tp="1h")


def test_timeline_shows_early_material(live_paths, tmp_path):
    led = Ledger(live_paths.ledger_path)
    led.add_prediction(RK, "24h", "ml_lgbm", "live-test0001",
                       _pred([1, 2, 3], card_at="2026-11-20T10:00:00+09:00"), "i", "m", "c")
    led.commit()
    led.close()
    src = Source(live_paths, "live")
    out = pages.timeline_page({"token": "t", "mode": "live"}, src, Store(tmp_path / "p.sqlite"), {"rk": RK})
    assert "早めの参考予想" in out and "11/20 10:00" in out


def test_compare_counts_shown_integrated_candidate(live_paths, tmp_path):
    src = Source(live_paths, "live")
    src.preds[RK].pop(("int_market_ml", "15m"))
    src.preds[RK][("int_market_ml_mam", "15m")] = _pred([3, 11, 7, 1, 2])
    src.preds[RK].pop(("rule", "15m"))
    assert src.pick(RK, "int", "15m")[0] == "int_market_ml_mam"
    out = pages.compare_page({"token": "t", "mode": "live"}, src, Store(tmp_path / "p.sqlite"), {"rk": RK, "tp": "15m"})
    assert "1つだけ" not in out and "一致" in out


def test_notify_scratch_after_a_was_sent(live_paths, tmp_path, monkeypatch):
    from keiba.app import server as srv
    app = App(live_paths, tmp_path / "p.sqlite")
    monkeypatch.setattr(checks, "now_jst", lambda: datetime(2026, 11, 22, 15, 30, tzinfo=checks.JST))
    src = app.cache.get("live")
    sent = []
    stop = threading.Event()
    t = threading.Thread(target=srv.notify_loop, args=(app, stop, 0.05, 20, lambda a, b: sent.append(b)), daemon=True)
    t.start()
    import time
    time.sleep(0.3)
    assert len(sent) == 1 and "自信度A" in sent[0] and "取消" not in sent[0]
    src.scratched_now[RK] = {11}          # 自信度Aを知らせた後に取消が分かった
    time.sleep(0.3)
    stop.set()
    t.join(2)
    assert len(sent) == 2 and "取消" in sent[1] and "11番" in sent[1] and "自信度A" not in sent[1]


def test_old_form_after_mode_switch_asks_first(server):
    app, port = server
    form = {"token": app.token, "rk": RK, "kind": "buy", "m": "ml_lgbm", "tp": "15m", "fm": "check",
            "combo": ["3-7-11"], "yen": ["100"], "memo": ""}
    origin = f"http://127.0.0.1:{port}"
    code, body, _ = _req(port, "/buy", form, origin=origin)     # 表示は live（Cookie なし）、フォームは練習で開いた
    assert code == 409 and "どちらに記録する" in body and app.store.entries(data_mode="live") == []
    assert app.store.entries() == []
    code, _, _ = _req(port, "/buy", {**form, "as_mode": "check"}, origin=origin)
    assert code == 303
    (e,) = app.store.entries(data_mode="check")
    assert e["data_mode"] == "check" and e["total"] == 100
    code, _, _ = _req(port, "/buy", {**form, "fm": "live"}, origin=origin)   # 同じモードならそのまま
    assert code == 303 and len(app.store.entries(data_mode="live")) == 1


def test_two_weeks_reads_archived_snapshots(live_paths, tmp_path, monkeypatch):
    import datetime as dt
    from datetime import date as real_date
    old = (real_date.today() - dt.timedelta(days=10)).isoformat()
    too_old = (real_date.today() - dt.timedelta(days=20)).isoformat()
    live = live_paths.data_dir / "live"
    rows = [{"race_key": f"{old}|東京|5", "kind": "odds_unavailable", "time_point": "24h", "fetched_at": f"{old}T09:00:00+09:00"},
            {"race_key": f"{too_old}|東京|6", "kind": "odds_unavailable", "time_point": "24h", "fetched_at": f"{too_old}T09:00:00+09:00"}]
    (live / "snapshots.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    src = Source(live_paths, "live")
    assert f"{old}|東京|5" in src.past_keys and f"{too_old}|東京|6" in src.past_keys   # 全期間の結果のために日付で区切らない
    out = pages.history_page({"token": "t", "mode": "live"}, src, Store(tmp_path / "p.sqlite"), {"f": "2w"})
    assert "東京5R" in out.replace(" ", "") and "東京6R" not in out.replace(" ", "")      # 2週間の表示は14日で区切る


def _autostart(tmp_path, *args, stop="ok", start="ok", loaded=True):
    import subprocess
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "launchctl").write_text(f"""#!/bin/sh
echo "launchctl $1" >> "{tmp_path}/calls"
case "$1" in
print) {"exit 0" if loaded else 'echo "Could not find service" >&2; exit 113'} ;;
bootout) {"exit 0" if stop == "ok" else 'echo "fake bootout failed" >&2; exit 5'} ;;
bootstrap) {"exit 0" if start == "ok" else 'echo "fake bootstrap failed" >&2; exit 5'} ;;
esac
""")
    (bindir / "launchctl").chmod(0o755)
    home = tmp_path / "home"
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home)}
    repo = Path(__file__).resolve().parents[1]
    return subprocess.run(["sh", str(repo / "tools/app/autostart.sh"), *args], env=env, capture_output=True, text=True)


def test_failed_reinstall_keeps_current_plist(tmp_path):
    plist = tmp_path / "home/Library/LaunchAgents/com.heroines.keiba-app.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("OLD")
    r = _autostart(tmp_path, "install", stop="refused")
    assert r.returncode != 0 and plist.read_text() == "OLD"          # 止められなければ触らない
    r = _autostart(tmp_path, "install", start="refused")
    assert r.returncode != 0 and plist.read_text() == "OLD"          # 新しい設定で登録できなければ前のまま
    assert "前の設定でも登録し直せなかった" in r.stderr                # 模擬は前の設定の登録も断る：そう伝える
    r = _autostart(tmp_path, "install")
    assert r.returncode == 0 and "com.heroines.keiba-app" in plist.read_text()
    first = tmp_path / "first"
    first.mkdir()
    r = _autostart(first, "install", loaded=False)                    # 初めて：LaunchAgents のフォルダも無い
    assert r.returncode == 0 and (first / "home/Library/LaunchAgents/com.heroines.keiba-app.plist").exists()


# ---------------- 再検証（💚20261120-12）の反例 ----------------

def test_duplicate_result_numbers_are_held():
    from keiba.app.data import make_result
    res = make_result([(1, 1), (2, 2), (1, 1), (2, 2)], 4, set(), {}, rows=4, field_size=4)
    assert res["complete"] is False and "重複" in res["incomplete"]
    st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-4": 100}}, res)
    assert st["status"] == "partial_result" and not st["counted"]


def test_card_with_more_rows_than_planned():
    from keiba.app.data import card_problem
    assert "多い" in card_problem(_card([1, 2, 3, 4, 5, 6, 7]), 6)
    assert card_problem(_card([1, 2, 3, 4, 5, 6]), 6) is None


def test_archived_race_detail_opens(live_paths, tmp_path):
    from datetime import date as real_date
    old = (real_date.today() - timedelta(days=10)).isoformat()
    live = live_paths.data_dir / "live"
    (live / "snapshots.jsonl").write_text(json.dumps({"race_key": f"{old}|東京|5", "kind": "odds_unavailable",
                                                      "time_point": "24h", "fetched_at": f"{old}T09:00:00+09:00"},
                                                     ensure_ascii=False) + "\n")
    src = Source(live_paths, "live")
    out = pages.history_detail_page({"token": "t", "mode": "live"}, src, Store(tmp_path / "p.sqlite"), {"rk": f"{old}|東京|5"})
    assert "見つからない" not in out and "東京" in out


def test_chosen_mode_switches_view_after_save(server):
    app, port = server
    form = {"token": app.token, "rk": RK, "kind": "buy", "m": "ml_lgbm", "tp": "15m", "fm": "check",
            "combo": ["3-7-11"], "yen": ["100"], "memo": "", "as_mode": "check"}
    code, _, headers = _req(port, "/buy", form, origin=f"http://127.0.0.1:{port}")
    assert code == 303 and "mode=check" in headers.get("Set-Cookie", "")     # 保存した練習の記録が見える表示へ


def test_failed_plist_move_rolls_back_running_job(tmp_path):
    agents = tmp_path / "home/Library/LaunchAgents"
    agents.mkdir(parents=True)
    plist = agents / "com.heroines.keiba-app.plist"
    plist.write_text("OLD")
    agents.chmod(0o555)          # 置き換え（mv）だけが失敗する
    try:
        r = _autostart(tmp_path, "install")
    finally:
        agents.chmod(0o755)
    calls = (tmp_path / "calls").read_text().split("\n")
    assert r.returncode == 1 and plist.read_text() == "OLD"
    assert "置けなかった" in r.stderr and "前の設定で登録し直した" in r.stderr
    # 新しい登録のあとに外して、前の設定で登録し直している
    assert calls[-4:-1] == ["launchctl bootstrap", "launchctl bootout", "launchctl bootstrap"]


# ---------------- 再検証（💚20261121-3）の反例 ----------------

def test_duplicate_scratch_rows_are_held():
    from keiba.app.data import make_result
    # 予定4頭：1・2完走、3取消の行が2つ、4の行が無い
    res = make_result([(1, 1), (2, 2)], 2, {3}, {}, rows=4, field_size=4, numbers=[1, 2, 3, 3])
    assert res["complete"] is False and "重複" in res["incomplete"]
    st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-4": 100}}, res)
    assert st["status"] == "partial_result" and not st["counted"]


def test_missing_tie_payout_is_detected_for_wallet():
    from keiba.settle import missing_payouts
    fin = [(1, 1), (2, 2), (3, 3), (4, 3)]
    assert missing_payouts({"1-2-4": 100}, fin, set(), {"1-2-3": 500}) == {"1-2-4"}
    assert missing_payouts({"1-2-3": 100}, fin, set(), {"1-2-3": 500}) == set()
    assert missing_payouts({"1-2-4": 100}, fin, {4}, {"1-2-3": 500}) == set()     # 取消の返還になる組は待たない


def test_latest_roster_rejects_horse_only_in_old_prediction():
    race = {"race_date": "2026-11-22", "post_time": "15:40"}
    p = _pred([1, 2, 3, 4, 5, 6, 7])
    names = {n: "" for n in range(1, 7)}
    items = {i["label"]: i for i in checks.pre_publish_check(p, {"1-2-7": 100}, race, names, set())}
    assert items["組の形（3頭・出走している馬番）"]["ok"] is False and "7" in items["組の形（3頭・出走している馬番）"]["detail"]
    ok = {i["label"]: i for i in checks.pre_publish_check(p, {"1-2-3": 100}, race, names, set())}
    assert ok["組の形（3頭・出走している馬番）"]["ok"] is True


def test_archived_race_reads_result_and_opens_all_pages(live_paths, tmp_path):
    from datetime import date as real_date
    from tests.test_live_wallet import result_html
    old = (real_date.today() - timedelta(days=10)).isoformat()
    rk = f"{old}|東京|5"
    live = live_paths.data_dir / "live"
    (live / "snapshots").mkdir(exist_ok=True)
    (live / "snapshots" / "r.html").write_text(result_html([3, 1, 2, 4]))
    _archive_card(live, rk, old, 4)
    _meta(live, rk, 4)
    with open(live / "snapshots.jsonl", "a") as f:
        f.write(json.dumps({"race_key": rk, "kind": "result", "time_point": "result",
                            "path": "snapshots/r.html", "fetched_at": f"{old}T16:00:00+09:00"}, ensure_ascii=False) + "\n")
    src = Source(live_paths, "live")
    assert src.results[rk]["payouts"] == {"1-2-3": 4560} and old not in src.days()
    store = Store(tmp_path / "p.sqlite")
    store.add(rk, "buy", [("1-2-3", "100")], "", None, None, "live")
    ctx = {"token": "t", "mode": "live"}
    detail = pages.history_detail_page(ctx, src, store, {"rk": rk})
    assert "4,560" in detail and "結果待ち" not in detail
    for fn, q in ((pages.timeline_page, {"rk": rk}), (pages.buy_form, {"rk": rk, "kind": "buy"})):
        assert "見つからない" not in fn(ctx, src, store, q)
    assert "¥4,560" in pages.purchases_page(ctx, src, store, {})


# ---------------- 再検証（💚20261121-7）の反例 ----------------

def _meta(live, rk, field_size, post_time="09:45"):
    """収集が予定表から外すときに残す記録（data/live/races_archive.jsonl）を1行足す。"""
    with open(live / "races_archive.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"race_key": rk, "field_size": field_size, "post_time": post_time}, ensure_ascii=False) + "\n")


def _archive_card(live, rk, day, n, post="9時45分", numbers=None):
    from tests.test_live_wallet import card_html
    (live / "snapshots").mkdir(exist_ok=True)
    name = f"card-{rk.replace('|', '-')}.html"
    html = card_html([f"{i:010d}" for i in range(n)])
    if numbers is not None:   # 読めた馬番だけの出馬表（行が欠けた原本）
        rows = html.split("<tr>")[1:]
        html = "<table><tbody>" + "".join("<tr>" + r for i, r in enumerate(rows) if i + 1 in numbers).replace("</tbody></table>", "") + "</tbody></table>"
    # 実物と同じく、発走時刻はタグをまたぐ
    (live / "snapshots" / name).write_text(f'<div class="cell time">発走時刻：<strong>{post}</strong></div>' + html)
    with open(live / "snapshots.jsonl", "a") as f:
        f.write(json.dumps({"race_key": rk, "kind": "card", "time_point": "24h", "path": f"snapshots/{name}",
                            "fetched_at": f"{day}T09:00:00+09:00"}, ensure_ascii=False) + "\n")


def _archive_result(live, rk, day, order, name=None, payout=True):
    from tests.test_live_wallet import result_html
    name = name or f"res-{rk.replace('|', '-')}.html"
    html = result_html(order)
    if not payout:
        html = html.split('<li class="trio"')[0]
    if name != "missing.html":
        (live / "snapshots" / name).write_text(html)
    with open(live / "snapshots.jsonl", "a") as f:
        f.write(json.dumps({"race_key": rk, "kind": "result", "time_point": "result", "path": f"snapshots/{name}",
                            "fetched_at": f"{day}T16:00:00+09:00"}, ensure_ascii=False) + "\n")


def test_archived_partial_result_is_held(live_paths, tmp_path):
    from datetime import date as real_date
    live = live_paths.data_dir / "live"
    for days in (8, 13):
        d = (real_date.today() - timedelta(days=days)).isoformat()
        rk = f"{d}|東京|{days}"
        _archive_card(live, rk, d, 4)
        _meta(live, rk, 4, None)                                  # 控えには頭数だけ（発走時刻は原本から読む）
        _archive_result(live, rk, d, [1, 2], payout=False)       # 4頭のうち2行だけ
    nocard = (real_date.today() - timedelta(days=9)).isoformat() + "|東京|9"
    _archive_result(live, nocard, nocard[:10], [1, 2], payout=False)
    src = Source(live_paths, "live")
    for days in (8, 13):
        d = (real_date.today() - timedelta(days=days)).isoformat()
        rk = f"{d}|東京|{days}"
        assert src.races[rk]["field_size"] == 4 and src.races[rk]["post_time"] == "09:45"   # 原本から取り直す
        assert src.results[rk]["complete"] is False
        st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-3": 100}}, src.results[rk])
        assert st["status"] == "partial_result" and not st["counted"]
    assert src.results[nocard]["complete"] is False            # 出馬表が無く頭数が分からなければ保留


def test_one_missing_archive_does_not_hide_other_results(live_paths):
    from datetime import date as real_date
    live = live_paths.data_dir / "live"
    a = (real_date.today() - timedelta(days=8)).isoformat()
    b = (real_date.today() - timedelta(days=9)).isoformat()
    _archive_result(live, f"{a}|東京|1", a, [1, 2, 3, 4], name="missing.html")   # 原本のファイルが無い
    _archive_card(live, f"{b}|東京|2", b, 4)
    _meta(live, f"{b}|東京|2", 4)
    _archive_result(live, f"{b}|東京|2", b, [3, 1, 2, 4])
    src = Source(live_paths, "live")
    assert f"{a}|東京|1" not in src.results
    assert src.results[f"{b}|東京|2"]["complete"] is True and src.results[f"{b}|東京|2"]["payouts"]
    assert any("結果を読めない" in n for n in src.notes)


# ---------------- 再検証（💚20261121-10）の反例 ----------------

def test_partial_card_and_result_do_not_guess_field_size(live_paths):
    from datetime import date as real_date
    live = live_paths.data_dir / "live"
    d = (real_date.today() - timedelta(days=8)).isoformat()
    rk = f"{d}|東京|3"
    _archive_card(live, rk, d, 4, numbers=[1, 2])      # 出馬表も2行だけ
    _archive_result(live, rk, d, [1, 2], payout=False)  # 結果も同じ2行
    src = Source(live_paths, "live")
    assert src.results[rk]["complete"] is False         # 読めた行の数から頭数を決めない
    st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-3": 100}}, src.results[rk])
    assert st["status"] == "partial_result" and not st["counted"]


def test_app_does_not_write_race_files(live_paths):
    Source(live_paths, "live")
    assert not (live_paths.data_dir / "app" / "race_meta.json").exists()   # 確認アプリは控えを書かない（収集の記録を読むだけ）


def test_real_card_post_time_across_tags():
    from keiba.app.data import card_post_time
    page = '<div class="cell time">\n        発走時刻：<strong>9時45分</strong>\n      </div>'
    assert card_post_time(page) == "09:45"


def test_missing_active_snapshot_keeps_other_results(live_paths, tmp_path):
    from datetime import date as real_date
    live = live_paths.data_dir / "live"
    state = json.loads((live / "state.json").read_text())
    other = "2026-11-22|東京|1"
    state["races"][other] = {"race_date": "2026-11-22", "venue": "東京", "race_number": 1, "post_time": "10:00", "field_size": 4}
    (live / "state.json").write_text(json.dumps(state, ensure_ascii=False))
    (live / "snapshots").mkdir(exist_ok=True)
    _archive_result(live, RK, "2026-11-22", [1, 2, 3, 4, 5, 6, 7, 8], name="missing.html")   # 予定中のレースの原本が無い
    _archive_result(live, other, "2026-11-22", [3, 1, 2, 4])
    src = Source(live_paths, "live")
    assert RK not in src.results and src.results[other]["complete"] is True
    from keiba.live.wallet import _snapshots, live_tables
    _, _, inputs = live_tables(live, state, _snapshots(live))      # 仮想財布の読取も、そのレースだけ飛ばす
    assert (other, "result") in inputs and (RK, "result") not in inputs


def test_legacy_unsettled_rows_are_recomputed_for_display(live_paths, tmp_path):
    live = live_paths.data_dir / "live"
    led = Ledger(live_paths.ledger_path)
    led.add_settlement(RK, "15m", "ml_lgbm", "live-test0001", "kelly",
                       {"status": "unsettled_no_payout", "stake": 2000, "returned": 0, "hits": []})
    led.commit()
    led.close()
    (live / "snapshots").mkdir(exist_ok=True)
    _archive_result(live, RK, "2026-11-22", [3, 7, 11, 1, 2, 4, 5, 6])
    src = Source(live_paths, "live")
    row = next(s for s in src.settlements if s["wallet"] == "kelly")
    assert row["status"] == "settled" and row["recomputed"] and row["returned"] == 1200 * 4560 // 100
    tbl = {x["method"]: x for x in pages.wallet_table(src, "kelly")}
    assert tbl["ml_lgbm"]["pending"] == 0 and tbl["ml_lgbm"]["returned"] == 1200 * 4560 // 100


# ---------------- 再検証（💚20261121-15）の反例 ----------------

def test_bad_archive_values_are_held(live_paths):
    from datetime import date as real_date
    live = live_paths.data_dir / "live"
    keys = []
    for i, bad in enumerate(("not-a-number", True, -1, 0, None, 2.0)):
        d = (real_date.today() - timedelta(days=8 + i)).isoformat()
        rk = f"{d}|東京|{i + 1}"
        keys.append(rk)
        _archive_card(live, rk, d, 4, numbers=[1, 2])
        _archive_result(live, rk, d, [1, 2], payout=False)
        _meta(live, rk, bad)
    src = Source(live_paths, "live")
    for rk in keys:
        st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-3": 100}}, src.results[rk])
        assert st["status"] == "partial_result" and not st["counted"], rk
    assert any("おかしい値" in n for n in src.notes)


def test_broken_archive_lines_do_not_break_pages(live_paths, tmp_path):
    from datetime import date as real_date
    live = live_paths.data_dir / "live"
    d = (real_date.today() - timedelta(days=8)).isoformat()
    good = f"{d}|東京|1"
    _archive_card(live, good, d, 4)
    _archive_result(live, good, d, [3, 1, 2, 4])
    with open(live / "races_archive.jsonl", "a", encoding="utf-8") as f:
        f.write("[1, 2]\nnull\n{broken\n" + json.dumps({"race_key": None}) + "\n" + '"文字列"\n')
    _meta(live, good, 4)
    (live / "snapshots").mkdir(exist_ok=True)
    _archive_result(live, RK, "2026-11-22", [3, 7, 11, 1, 2, 4, 5, 6])      # 予定中のレース
    src = Source(live_paths, "live")
    assert src.results[good]["complete"] is True and src.results[RK]["complete"] is True
    store = Store(tmp_path / "p.sqlite")
    ctx = {"token": "t", "mode": "live", "theme": "auto", "size": "normal", "refresh": "off"}
    for fn, q in ((pages.races_page, {}), (pages.history_page, {"f": "2w"}), (pages.results_page, {})):
        assert "<html" in fn(ctx, src, store, q)


def test_archive_change_reloads_cache(live_paths):
    live = live_paths.data_dir / "live"
    c = Cache(live_paths)
    a = c.get("live")
    _meta(live, "2026-11-01|東京|1", 4)
    assert c.get("live") is not a


def test_wallet_skips_empty_snapshot_and_unknown_size(tmp_path):
    from keiba.live.wallet import _snapshots, live_tables
    from tests.test_live_wallet import result_html
    live = tmp_path / "live"
    (live / "snapshots").mkdir(parents=True)
    state = {"races": {
        "2026-11-22|東京|1": {"race_date": "2026-11-22", "venue": "東京", "race_number": 1, "post_time": "10:00", "field_size": 4},
        "2026-11-22|東京|2": {"race_date": "2026-11-22", "venue": "東京", "race_number": 2, "post_time": "10:30", "field_size": 4}}}
    (live / "snapshots" / "empty.html").write_text("")                   # 0バイトの原本
    (live / "snapshots" / "ok.html").write_text(result_html([3, 1, 2, 4]))
    rows = [{"race_key": "2026-11-22|東京|1", "kind": "result", "time_point": "result", "path": "snapshots/empty.html",
             "fetched_at": "2026-11-22T11:00:00+09:00"},
            {"race_key": "2026-11-22|東京|2", "kind": "result", "time_point": "result", "path": "snapshots/ok.html",
             "fetched_at": "2026-11-22T11:30:00+09:00"}]
    (live / "snapshots.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    _, _, inputs = live_tables(live, state, _snapshots(live))
    assert ("2026-11-22|東京|2", "result") in inputs                       # 空の原本は、そのレースだけ飛ばす
    from keiba.settle import result_problem
    assert result_problem([(1, 1), (2, 2)], 2, set(), {}, 2, None)          # 頭数が分からなければ精算しない
    assert result_problem([(1, 1), (2, 2)], 2, set(), {}, 2, True)


# ---------------- 受け入れ検証（💚20261121-17）の「止める」4件 ----------------

def test_blank_rank_holds_dead_heat_settlement():
    from keiba.settle import result_problem
    from keiba.app.data import make_result
    fin = [(1, 1), (2, 2), (3, 3), (4, None)]          # 4番の着順が空欄（途中まで読めたページ）
    assert result_problem(fin, 4, set(), {"1-2-3": 500}, 4, 4)
    res = make_result(fin, 4, set(), {"1-2-3": 500}, 4, 4, [1, 2, 3, 4])
    st = settle_purchase({"kind": "buy", "total": 100, "stakes": {"1-2-4": 100}}, res)
    assert st["status"] == "partial_result" and not st["counted"]


def test_interrupted_snapshot_index_keeps_other_rows(live_paths, tmp_path):
    live = live_paths.data_dir / "live"
    (live / "snapshots").mkdir(exist_ok=True)
    _archive_result(live, RK, "2026-11-22", [3, 7, 11, 1, 2, 4, 5, 6])
    with open(live / "snapshots.jsonl", "a") as f:
        f.write('{"race_key": "2026-11-22|東京|9", "kind": "res')        # 追記が途中で止まった
    from keiba.live.collector import append_lines
    append_lines(live / "snapshots.jsonl", [json.dumps({"race_key": "2026-11-22|東京|9", "kind": "odds_unavailable",
                                                         "time_point": "1h", "fetched_at": "2026-11-22T10:00:00+09:00"})])
    lines = (live / "snapshots.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["kind"] == "odds_unavailable"        # 新しい行は途中の行にくっつかない
    src = Source(live_paths, "live")
    assert src.results[RK]["complete"] is True                          # ほかの行は読める
    from keiba.live.wallet import _snapshots
    assert len(_snapshots(live)) == len(lines) - 1


def test_ledger_lock_keeps_previous_data(live_paths, tmp_path, monkeypatch):
    from keiba.app import data as d
    monkeypatch.setattr(d, "LEDGER_TIMEOUT", 0.1)
    cache = Cache(live_paths)
    first = cache.get("live")
    lock = sqlite3.connect(live_paths.ledger_path)
    lock.execute("BEGIN EXCLUSIVE")                         # 収集が書いている最中
    try:
        (live_paths.data_dir / "live" / "state.json").touch()
        import os
        os.utime(live_paths.data_dir / "live" / "state.json", (1, 1))   # 元のファイルが変わった（読み直しが要る）
        again = cache.get("live")
        assert again is first and again.stale and "書込み中" in again.stale   # 前に読んだデータで表示を続ける
        empty = Cache(live_paths)
        with pytest.raises(d.LedgerBusy):                     # 一度も読めていなければ「読み込み中」
            empty.get("live")
    finally:
        lock.rollback()
        lock.close()
    assert cache.get("live") is not first                    # 書込みが終われば読み直す


def test_ledger_busy_page(server, monkeypatch):
    app, port = server
    from keiba.app import data as d

    def busy(mode):
        raise d.LedgerBusy("locked")
    monkeypatch.setattr(app.cache, "get", busy)
    code, body, _ = _req(port, "/races")
    assert code == 503 and "台帳を書いている" in body and 'http-equiv="refresh"' in body
    data = {"token": app.token, "rk": RK, "kind": "buy", "combo": ["1-2-3"], "yen": ["100"]}
    code, body, _ = _req(port, "/buy", data, origin=f"http://127.0.0.1:{port}")
    assert code == 503 and "保存していない" in body and app.store.entries() == []
