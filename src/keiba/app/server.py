"""確認アプリのサーバー（Python の標準ライブラリだけ）。

  - ふだんは 127.0.0.1 にだけつなぐ（家のネットワークには出さない）。Host がこのMac以外なら断る
  - --lan のときだけ同じWi-Fiから開ける。合言葉（.env の KEIBA_APP_PASSPHRASE）が無いと起動しない。
    このMac以外からは合言葉を入れるまで何も見せない（5回まちがえると10分止める）
  - 書き込み（購入記録・メモ・設定）は POST で、起動ごとに作る合言葉（token）が合うときだけ受け付ける
  - 予想の台帳は読取専用。書き込むのは data/app/purchases.sqlite（追記専用）だけ
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import traceback
from pathlib import Path
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import pages
from .data import MODES, Cache
from .store import InputError, Store

HOST = "127.0.0.1"
MAX_BODY = 64 * 1024
MIN_PASSPHRASE = 10
LOGIN_LIMIT, LOGIN_LOCK_SEC = 5, 600


def read_passphrase(repo_root: Path) -> str:
    """環境変数か、リポジトリの .env（git には上げない）から読む。"""
    v = os.environ.get("KEIBA_APP_PASSPHRASE", "")
    env = repo_root / ".env"
    if not v and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("KEIBA_APP_PASSPHRASE="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
    return v


def lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # 送信はしない（経路の決定だけ）
            return s.getsockname()[0]
    except OSError:
        return None


class App:
    def __init__(self, paths, store_path=None, default_mode="live", passphrase: str | None = None):
        self.paths = paths
        self.default_mode = default_mode if default_mode in MODES else "live"
        self.cache = Cache(paths)
        self.store = Store(store_path or paths.data_dir / "app" / "purchases.sqlite")
        self.token = secrets.token_urlsafe(24)
        self.lan = bool(passphrase)
        self.passphrase = passphrase or ""
        # 合言葉が変わると古いログインは無効になる（再起動しても同じ値）
        self.auth_value = hmac.new(self.passphrase.encode(), b"keiba-app-login-v1", hashlib.sha256).hexdigest() if passphrase else ""
        self.failures: dict[str, list[float]] = {}
        self._cmp_cache: dict = {}

    def compare(self, va: str, vb: str) -> dict:
        from .data import version_compare
        lp = self.paths.ledger_path
        key = (va, vb, lp.stat().st_mtime_ns if lp.exists() else 0)
        if key not in self._cmp_cache:
            self._cmp_cache = {key: version_compare(lp, va, vb)}
        return self._cmp_cache[key]

    def locked(self, ip: str) -> bool:
        now = time.time()
        self.failures[ip] = [t for t in self.failures.get(ip, []) if now - t < LOGIN_LOCK_SEC]
        return len(self.failures[ip]) >= LOGIN_LIMIT

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
    "/race/compare": pages.compare_page, "/race/timeline": pages.timeline_page, "/race/check": pages.check_page,
    "/buy/new": pages.buy_form,
    "/history": pages.history_page, "/history/detail": pages.history_detail_page, "/note/edit": pages.note_form,
    "/results": pages.results_page, "/results/methods": pages.results_methods_page,
    "/results/conditions": pages.results_conditions_page, "/results/skips": pages.results_skips_page,
    "/improve": pages.improve_page, "/improve/history": pages.improve_history_page,
    "/menu": pages.menu_page, "/purchases": pages.purchases_page, "/settings": pages.settings_page, "/about": pages.about_page,
}


def make_handler(app: App, port: int, extra_hosts=()):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", *extra_hosts}

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
                    "size": c.get("size") if c.get("size") in ("normal", "large") else "normal",
                    "refresh": c.get("refresh") if c.get("refresh") in ("on", "off") else "on",
                    "token": app.token, "lan": app.lan}

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def _local(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1")

        def _authed(self) -> bool:
            if self._local():
                return True
            if not app.lan:
                return False
            return hmac.compare_digest(self._cookies().get("auth", ""), app.auth_value)

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return not origin or origin.split("://", 1)[-1] in allowed_hosts

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
            if not self._authed():
                if not app.lan:
                    return self._deny()
                return self._send(200 if u.path == "/login" else 401, pages.login_page(ctx))
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
                if u.path == "/improve/versions":
                    return self._send(200, pages.improve_versions_page(ctx, src, app.store, q, app.compare))
                if u.path == "/login":
                    return self._redirect("/races")
                if u.path in ("/races", "/race"):
                    ctx["health"] = app.extra_status().get("health")
                fn = GET_ROUTES.get(u.path)
                if not fn:
                    return self._send(404, pages.layout(ctx, "見つからない", pages.empty("ページが見つからないよ", ""), None, "/races"))
                self._send(200, fn(ctx, src, app.store, q))
            except Exception:
                traceback.print_exc()
                self._send(500, pages.layout(ctx, "エラー", pages.empty("表示できなかったよ", "端末にくわしい内容を出したよ。"), None, "/races"))

        # ---- POST ----
        def do_POST(self):
            if not self._host_ok() or not self._origin_ok():
                return self._deny()
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large", "text/plain")
            form = parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
            one = {k: v[-1] for k, v in form.items()}
            if urlparse(self.path).path == "/login":
                return self._post_login(one)
            if not self._authed():
                return self._deny() if not app.lan else self._redirect("/login")
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
                    for key, ok in (("mode", MODES), ("theme", ("auto", "light", "dark")), ("size", ("normal", "large")),
                                    ("refresh", ("on", "off"))):
                        if one.get(key) in ok:
                            cookies.append(f"{key}={one[key]}; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly")
                    return self._redirect("/menu", cookies)
                if u.path == "/improve/decision":
                    va, vb, d = one.get("a", ""), one.get("b", ""), one.get("decision", "")
                    if d not in pages.DECISIONS or not va or not vb:
                        return self._send(400, pages.layout(ctx, "判断", pages.empty("どれか1つ選んでね", ""), "improve",
                                                            pages.url("/improve/versions", a=va, b=vb)))
                    from datetime import datetime
                    text = f"{pages.DECISIONS[d]}（{datetime.now():%Y-%m-%d %H:%M}）" + (f"\n{one['memo'].strip()}" if one.get("memo", "").strip() else "")
                    app.store.set_note(pages.decision_key(va, vb), text)
                    return self._redirect(pages.url("/improve/versions", a=va, b=vb))
                return self._send(404, "not found", "text/plain")
            except Exception:
                traceback.print_exc()
                self._send(500, pages.layout(ctx, "エラー", pages.empty("保存できなかったよ", "端末にくわしい内容を出したよ。"), None, "/races"))

        def _post_login(self, one):
            ctx = self._ctx()
            if not app.lan:
                return self._redirect("/races")
            ip = self.client_address[0]
            if app.locked(ip):
                return self._send(429, pages.login_page(ctx, "まちがいが続いたので、10分待ってからもう一度ね"))
            if hmac.compare_digest(one.get("passphrase", "").encode(), app.passphrase.encode()):
                app.failures.pop(ip, None)
                return self._redirect("/races", [f"auth={app.auth_value}; Path=/; Max-Age=2592000; SameSite=Strict; HttpOnly"])
            app.failures.setdefault(ip, []).append(time.time())
            time.sleep(0.5)
            return self._send(401, pages.login_page(ctx, "合言葉がちがうよ"))

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
            nxt = one.get("next", "")
            if nxt.startswith("/race?") and "//" not in nxt and "\\" not in nxt:   # 戻り先はこのアプリのレース画面だけ
                return self._redirect(nxt)
            return self._redirect(pages.url("/history/detail", rk=rk))

    return Handler


def notify_loop(app: App, stop: threading.Event, interval: int = 60, lead_min: int = 20, sender=None):
    """自信度Aで買い目のあるレースと、買い目に取消馬が残っているレースを、発走の少し前にMacの通知で知らせる（1レース1回）。"""
    from .checks import now_jst, post_datetime, pre_publish_check
    from .data import MAIN_WALLET, allocation_of
    sent: set = set()

    def send(title, text):
        esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{esc(text)}" with title "{esc(title)}"'],
                       check=False, timeout=10, capture_output=True)
    sender = sender or send
    while not stop.is_set():
        try:
            src = app.cache.get("live")
            now = now_jst()
            for rk, r in src.races.items():
                post = post_datetime(r)
                if not post or not (0 < (post - now).total_seconds() <= lead_min * 60):
                    continue
                tp = src.latest_tp(rk)
                p = src.pick(rk, "ml", tp)[1] if tp else None
                if not p:
                    continue
                stakes = allocation_of(p, MAIN_WALLET["live"]).get("stakes") or {}
                bad = [i for i in pre_publish_check(p, stakes, r) if i["ok"] is False and i["label"].startswith("取消")]
                title = f"{r['venue']}{r['race_number']}R {r.get('post_time') or ''}"
                if bad and (rk, "bad") not in sent:
                    sender(title, "買い目に取消馬が残っているよ。確認アプリで見てね")
                    sent.add((rk, "bad"))
                if p.get("confidence") == "A" and stakes and (rk, "A") not in sent:
                    sender(title, f"自信度A・三連複 {len(stakes)}点 ¥{sum(stakes.values()):,}")
                    sent.add((rk, "A"))
        except Exception:
            traceback.print_exc()
        stop.wait(interval)


def serve(paths, port: int = 8765, open_browser: bool = False, store_path=None, default_mode: str = "live",
          lan: bool = False, notify: bool = False):
    passphrase = None
    extra_hosts: list[str] = []
    host = HOST
    if lan:
        passphrase = read_passphrase(Path(__file__).resolve().parents[3])
        if len(passphrase) < MIN_PASSPHRASE:
            raise SystemExit(f"--lan には合言葉が要るよ：~/keiba-prototype/.env に KEIBA_APP_PASSPHRASE=（{MIN_PASSPHRASE}文字以上）を書いてね")
        ip = lan_address()
        extra_hosts = [f"{x}:{port}" for x in (ip, f"{socket.gethostname().split('.')[0]}.local") if x]
        host = "0.0.0.0"
    app = App(paths, store_path, default_mode, passphrase)
    httpd = ThreadingHTTPServer((host, port), make_handler(app, port, extra_hosts))
    addr = f"http://127.0.0.1:{port}/"
    print(f"確認アプリ：{addr}（止めるときは Ctrl+C）")
    if lan:
        print("同じWi-FiのiPhoneから：" + "、".join(f"http://{x}/" for x in extra_hosts) + "（合言葉が要る）")
    else:
        print("このMacの中だけ（家のネットワークには出していない）")
    print(f"購入記録の保存先：{app.store.path}")
    stop = threading.Event()
    if notify:
        threading.Thread(target=notify_loop, args=(app, stop), daemon=True).start()
        print("通知：自信度Aのレースと、買い目に取消馬が残るレースを、発走20分前からMacに出すよ")
    if open_browser:
        import webbrowser
        webbrowser.open(addr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
        app.store.close()
