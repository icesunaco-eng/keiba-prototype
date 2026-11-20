"""確認アプリ：購入記録（追記専用）・精算・画面・サーバーの守り（合成データ。ネットにはつながない）。"""
import json
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


def _res(fin, finishers=None, scratched=(), payouts=None):
    from keiba.app.data import make_result
    return make_result(fin, len(fin) if finishers is None else finishers, set(scratched), payouts or {})


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

def _pred(order, conf="A", stakes=None, status="allocated", scratched=()):
    horses = [{"n": n, "p_top3": round(0.9 - i * 0.1, 3)} for i, n in enumerate(order)]
    alloc = {a: {"status": status, "stakes": stakes or {}, "total": sum((stakes or {}).values())}
             for a in ("ev_weighted", "equal_payout", "kelly")}
    return {"race_date": "2026-11-22", "horses": horses, "confidence": conf, "period": "live",
            "trios": [{"combo": "-".join(map(str, sorted(order[:3]))), "p": 0.2}], "allocation": alloc,
            "inputs": {"card_fetched_at": "2026-11-22T14:30:00+09:00", "scratched_known": list(scratched), "win_odds_ok": True},
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
    led.add_prediction(RK, "1h", "ml_lgbm", v, _pred([3, 7, 9, 1, 2], "B", {"3-7-9": 600}), "i", "m", "c")
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
