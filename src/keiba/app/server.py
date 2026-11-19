"""確認アプリのサーバー（Python の標準ライブラリだけ。このMacの中だけで動かす）。

  - 127.0.0.1 にだけつなぐ（家のネットワークには出さない）。Host がこのMac以外なら断る
  - 書き込み（購入記録・メモ・設定）は POST で、起動ごとに作る合言葉（token）が合うときだけ受け付ける
  - 予想の台帳は読取専用。書き込むのは data/app/purchases.sqlite（追記専用）だけ
"""
from __future__ import annotations

import json
import secrets
import traceback
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import pages
from .data import MODES, Cache
from .store import InputError, Store

HOST = "127.0.0.1"
MAX_BODY = 64 * 1024


class App:
    def __init__(self, paths, store_path=None, default_mode="live"):
        self.paths = paths
        self.default_mode = default_mode if default_mode in MODES else "live"
        self.cache = Cache(paths)
        self.store = Store(store_path or paths.data_dir / "app" / "purchases.sqlite")
        self.token = secrets.token_urlsafe(24)

    def extra_status(self) -> dict:
        root = self.paths.data_dir / "live"
        out = {}
        f = root / "health.json"
        if f.exists():
            try:
                out["health"] = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                out["health"] = {"errors": ["health.json が読めない"]}
        oc = root / "odds_check.csv"
        if oc.exists():
            import csv
            from datetime import datetime
            rows = list(csv.DictReader(oc.open(encoding="utf-8")))
            out["odds"] = {"total": len(rows), "pass": sum(r.get("pass") == "True" for r in rows),
                           "file_time": f"{datetime.fromtimestamp(oc.stat().st_mtime):%m/%d %H:%M}"}
        return out

    def metrics(self) -> dict | None:
        f = self.paths.data_dir / "eval" / "metrics.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        except ValueError:
            return None


GET_ROUTES = {
    "/races": pages.races_page, "/race": pages.race_page, "/race/horses": pages.horses_page,
    "/race/compare": pages.compare_page, "/race/timeline": pages.timeline_page, "/buy/new": pages.buy_form,
    "/history": pages.history_page, "/history/detail": pages.history_detail_page, "/note/edit": pages.note_form,
    "/results": pages.results_page, "/results/methods": pages.results_methods_page,
    "/results/conditions": pages.results_conditions_page, "/results/skips": pages.results_skips_page,
    "/improve": pages.improve_page, "/improve/history": pages.improve_history_page,
    "/menu": pages.menu_page, "/purchases": pages.purchases_page, "/settings": pages.settings_page, "/about": pages.about_page,
}


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "keiba-app"

        def log_message(self, fmt, *args):  # 端末を静かにする（エラーだけ出す）
            pass

        # ---- 共通 ----
        def _cookies(self) -> dict:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return {k: v.value for k, v in c.items()}

        def _ctx(self) -> dict:
            c = self._cookies()
            return {"mode": c.get("mode") if c.get("mode") in MODES else app.default_mode,
                    "theme": c.get("theme") if c.get("theme") in ("auto", "light", "dark") else "auto",
                    "size": c.get("size") if c.get("size") in ("normal", "large") else "normal", "token": app.token}

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def _send(self, status: int, body: str, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")  # no-referrer だとフォームの Origin が null になる
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'")
            for k, v in (headers or {}).items():
                for one in (v if isinstance(v, list) else [v]):
                    self.send_header(k, one)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to: str, cookies: list[str] | None = None):
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", to)
            self.send_header("Content-Length", "0")
            for c in cookies or []:
                self.send_header("Set-Cookie", c)
            self.end_headers()

        def _deny(self, msg="このMacの中からだけ使えるよ"):
            self._send(HTTPStatus.FORBIDDEN, msg, "text/plain; charset=utf-8")

        # ---- GET ----
        def do_GET(self):
            if not self._host_ok():
                return self._deny()
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            ctx = self._ctx()
            try:
                if u.path in ("/", ""):
                    return self._redirect("/races")
                if u.path == "/favicon.ico":
                    return self._send(HTTPStatus.NO_CONTENT, "")
                src = app.cache.get(ctx["mode"])
                if u.path == "/purchases.csv":
                    return self._send(200, "﻿" + pages.purchases_csv(src, app.store), "text/csv; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="keiba-purchases.csv"'})
                if u.path == "/status":
                    return self._send(200, pages.status_page(ctx, src, app.store, q, app.extra_status()))
                if u.path == "/improve/compare":
                    return self._send(200, pages.improve_compare_page(ctx, src, app.store, q, app.metrics()))
                fn = GET_ROUTES.get(u.path)
                if not fn:
                    return self._send(404, pages.layout(ctx, "見つからない", pages.empty("ページが見つからないよ", ""), None, "/races"))
                self._send(200, fn(ctx, src, app.store, q))
            except Exception:
                traceback.print_exc()
                self._send(500, pages.layout(ctx, "エラー", pages.empty("表示できなかったよ", "端末にくわしい内容を出したよ。"), None, "/races"))

        # ---- POST ----
        def do_POST(self):
            if not self._host_ok():
                return self._deny()
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in allowed_hosts:
                return self._deny()
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large", "text/plain")
            form = parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
            one = {k: v[-1] for k, v in form.items()}
            if not secrets.compare_digest(one.get("token", ""), app.token):
                return self._deny("画面が古いみたい。ページを開き直してから、もう一度送ってね")
            ctx = self._ctx()
            u = urlparse(self.path)
            try:
                if u.path == "/buy":
                    return self._post_buy(ctx, form, one)
                if u.path == "/buy/void":
                    e = app.store.entry(one.get("entry", ""))
                    if not e:
                        return self._send(404, "not found", "text/plain")
                    app.store.add(e["race_key"], "void", {}, e["memo"], e["method"], e["time_point"], e["data_mode"], e["entry_id"])
                    return self._redirect(pages.url("/history/detail", rk=e["race_key"]))
                if u.path == "/note":
                    rk = one.get("rk", "")
                    if not rk:
                        return self._send(400, "rk", "text/plain")
                    app.store.set_note(rk, one.get("text", ""))
                    return self._redirect(pages.url("/history/detail", rk=rk))
                if u.path == "/settings":
                    cookies = []
                    for key, ok in (("mode", MODES), ("theme", ("auto", "light", "dark")), ("size", ("normal", "large"))):
                        if one.get(key) in ok:
                            cookies.append(f"{key}={one[key]}; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly")
                    return self._redirect("/menu", cookies)
                return self._send(404, "not found", "text/plain")
            except Exception:
                traceback.print_exc()
                self._send(500, pages.layout(ctx, "エラー", pages.empty("保存できなかったよ", "端末にくわしい内容を出したよ。"), None, "/races"))

        def _post_buy(self, ctx, form, one):
            rk = one.get("rk", "")
            kind = one.get("kind", "buy")
            combos, yens = form.get("combo", []), form.get("yen", [])
            rows = list(zip(combos, yens))
            stakes: list[tuple[str, str]] = []
            src = app.cache.get(ctx["mode"])
            try:
                if not rk:
                    raise InputError("レースが分からない")
                for c, y in rows:
                    if not c.strip() and not y.strip():
                        continue
                    if not c.strip():
                        raise InputError(f"金額 {y} の組が空だよ")
                    if not y.strip():
                        raise InputError(f"{c} の金額が空だよ")
                    stakes.append((c.strip(), y.strip()))
                app.store.add(rk, kind, stakes, one.get("memo", ""), one.get("m") or None, one.get("tp") or None,
                              ctx["mode"], one.get("entry") or None)
            except InputError as exc:
                q = {"rk": rk, "m": one.get("m"), "tp": one.get("tp"), "kind": kind, "entry": one.get("entry")}
                body = pages.buy_form(ctx, src, app.store, q, str(exc), {"kind": kind, "rows": rows, "memo": one.get("memo", "")})
                return self._send(HTTPStatus.BAD_REQUEST, body)
            return self._redirect(pages.url("/history/detail", rk=rk))

    return Handler


def serve(paths, port: int = 8765, open_browser: bool = False, store_path=None, default_mode: str = "live"):
    app = App(paths, store_path, default_mode)
    httpd = ThreadingHTTPServer((HOST, port), make_handler(app, port))
    addr = f"http://127.0.0.1:{port}/"
    print(f"確認アプリ：{addr}（このMacの中だけ。止めるときは Ctrl+C）")
    print(f"購入記録の保存先：{app.store.path}")
    if open_browser:
        import webbrowser
        webbrowser.open(addr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.store.close()
