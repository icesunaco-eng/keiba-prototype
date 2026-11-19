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


def test_settle_purchase():
    e = {"kind": "buy", "stakes": {"1-2-3": 1000, "2-3-4": 200}, "total": 1200}
    assert settle_purchase(e, None, None)["status"] == "pending"
    st = settle_purchase(e, "1-2-3", 4560)
    assert st == {"status": "settled", "stake": 1200, "returned": 45600, "hit": "1-2-3"}
    assert settle_purchase(e, "5-6-7", 9000)["returned"] == 0
    assert settle_purchase(e, "1-2-3", None)["status"] == "no_payout"
    assert settle_purchase({"kind": "skip", "stakes": {}, "total": 0}, "1-2-3", 100)["stake"] == 0


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
