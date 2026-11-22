"""確認アプリの画面（サーバーで HTML を組み立てる。JavaScript は使わない）。

決まり（ごちゃつかせない）：1画面1テーマ、カードは3〜4枚まで。詳しいものは「›」で次のページへ。
よく使う4つは下のタブ（レース・履歴・結果・改善）、たまに使うものは右上のメニュー（≡）。
推論を事実として見せない（事実・推論・注意のラベルを付ける）。記録が無いところは「—」と件数0を出す。
"""
from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from .data import (ALLOC_LABEL, ALLOC_STATUS, MAIN_WALLET, METHOD_LABEL, METHOD_TABS, MODES, TIME_POINTS, TP_LABEL,
                   WALLET_LABEL, allocation_of, distance_band, horse_probs, is_skip, marks_of, probs_complete,
                   race_sort_key)
from .checks import (bias_check, card_flags, change_conditions, check_summary, horse_comment, material_timing, now_jst,
                     post_datetime, pre_publish_check, unknown_required)
from .store import SETTLE_LABEL, settle_purchase

WEEK = "月火水木金土日"
TABS = (("races", "レース", "flag"), ("history", "履歴", "clock"), ("results", "結果", "chart"), ("improve", "改善", "trend"))

ICONS = {  # 線だけの小さな絵（外部の読み込みはしない）
    "flag": '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
    "clock": '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    "chart": '<path d="M5 20V10m5 10V4m5 16v-8m5 8V8"/>',
    "trend": '<path d="M4 17l6-6 4 4 6-7"/><path d="M15 8h5v5"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "back": '<path d="M15 5l-7 7 7 7"/>',
}

CSS = """
:root{--bg:#f6f8ef;--card:#fff;--fg:#232a1b;--muted:#68705c;--line:#e2e8d4;--accent:#639922;--accent-strong:#3b6d11;
--soft:#eaf3de;--mid:#c0dd97;--on:#fff;--star-bg:#faeeda;--star-fg:#633806;--warn-bg:#fff3d1;--warn-fg:#7a5200;--bad:#a32d2d;--m0fg:#173404}
:root[data-theme=dark]{--bg:#121510;--card:#1b2017;--fg:#e8eddf;--muted:#a2aa94;--line:#2d3427;--accent:#97c459;--accent-strong:#c0dd97;
--soft:#24301a;--mid:#3d5a1f;--on:#14200a;--star-bg:#3a2c10;--star-fg:#f3c875;--warn-bg:#3a2e10;--warn-fg:#f0c870;--bad:#f09595;--m0fg:#e3f2cf}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121510;--card:#1b2017;--fg:#e8eddf;--muted:#a2aa94;--line:#2d3427;
--accent:#97c459;--accent-strong:#c0dd97;--soft:#24301a;--mid:#3d5a1f;--on:#14200a;--star-bg:#3a2c10;--star-fg:#f3c875;--warn-bg:#3a2e10;--warn-fg:#f0c870;--bad:#f09595;--m0fg:#e3f2cf}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Sans","Hiragino Kaku Gothic ProN",sans-serif}
:root[data-size=large] body{font-size:17px}
a{color:inherit;text-decoration:none}
.top{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;align-items:center;gap:8px;padding:10px 16px 6px;max-width:640px;margin:0 auto}
.top .ttl{flex:1;font-size:.85rem;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ib{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:12px;color:var(--fg)}
.ib:active{background:var(--soft)}
svg.i{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
main{max-width:640px;margin:0 auto;padding:0 16px 96px}
h1{font-size:1.5rem;font-weight:700;margin:2px 0 0;letter-spacing:.01em}
.lead{color:var(--muted);font-size:.85rem;margin:2px 0 14px}
h2{font-size:1rem;margin:0 0 6px;display:flex;align-items:center;gap:6px}
h2::before{content:"";width:4px;height:1em;border-radius:2px;background:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:12px 14px;margin:0 0 12px}
.card.hot{border:1.5px solid var(--accent);background:var(--soft)}
.card.flat{background:transparent;border-style:dashed}
.muted{color:var(--muted);font-size:.85rem}.small{font-size:.8rem}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.list>a,.list>div{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 0;border-top:1px solid var(--line)}
.list>a:first-child,.list>div:first-child{border-top:0}
.chev{color:var(--muted)}
.seg{display:flex;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px;margin:0 0 14px;gap:2px}
.seg a{flex:1;text-align:center;font-size:.85rem;padding:5px 0;border-radius:999px;color:var(--muted)}
.seg a.on{background:var(--accent);color:var(--on);font-weight:600}
.chips{display:flex;gap:6px;overflow-x:auto;margin:0 0 12px;padding-bottom:2px;scrollbar-width:none}.chips::-webkit-scrollbar{display:none}
.chips a{flex:none;font-size:.8rem;padding:4px 12px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--muted)}
.chips a.on{border-color:var(--accent);color:var(--accent-strong);background:var(--soft);font-weight:600}
.mk{display:inline-flex;align-items:center;gap:4px;border-radius:999px;padding:1px 10px;margin:4px 4px 0 0;background:var(--line);font-weight:600;font-variant-numeric:tabular-nums}
.mk.m0{background:var(--mid);color:var(--m0fg)}
.mk.m2{background:var(--star-bg);color:var(--star-fg)}
.mk .nm{font-weight:400;font-size:.85rem}
.badge{flex:none;display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;padding:0 6px;border-radius:10px;font-weight:700;background:var(--accent);color:var(--on)}
.badge.b{background:var(--mid);color:var(--accent-strong)}.badge.c{background:var(--line);color:var(--muted)}
.chip{display:inline-block;font-size:.75rem;padding:1px 9px;border-radius:999px;border:1px solid var(--line);color:var(--muted);margin-right:4px;white-space:nowrap}
.chip.ok{border-color:var(--accent);color:var(--accent-strong)}.chip.warn{background:var(--warn-bg);border-color:transparent;color:var(--warn-fg)}
.tag{display:inline-block;font-size:.7rem;padding:0 7px;border-radius:6px;margin-right:6px;background:var(--line);color:var(--muted)}
.tag.f{background:var(--mid);color:var(--accent-strong)}.tag.g{background:var(--star-bg);color:var(--star-fg)}.tag.w{background:var(--warn-bg);color:var(--warn-fg)}
.bet{display:flex;justify-content:space-between;padding:5px 0;border-top:1px dashed var(--line);font-variant-numeric:tabular-nums}
.bet:first-of-type{border-top:0}.bet.sum{border-top:1px solid var(--line);font-weight:700;color:var(--accent-strong)}
.kpi{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 12px}
.kpi div{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:10px 12px}
.kpi b{display:block;font-size:1.5rem;font-variant-numeric:tabular-nums}
.btns{display:flex;gap:10px;margin:4px 0 12px}
.btn{flex:1;display:inline-flex;justify-content:center;align-items:center;min-height:44px;border-radius:999px;border:1.5px solid var(--accent);color:var(--accent-strong);background:var(--card);font:inherit;font-weight:600;cursor:pointer}
.btn.pri{background:var(--accent);color:var(--on)}
.dots{display:flex;align-items:center;gap:0;margin:8px 0 2px}
.dots span{flex:1;text-align:center;font-size:.72rem;color:var(--muted);position:relative;padding-top:12px}
.dots span::before{content:"";position:absolute;top:0;left:50%;width:9px;height:9px;margin-left:-5px;border-radius:50%;border:1.5px solid var(--line);background:var(--card)}
.dots span.on::before{background:var(--accent);border-color:var(--accent)}
.tl{border-left:2px solid var(--mid);margin-left:6px;padding-left:14px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:7px 4px;border-bottom:1px solid var(--line);text-align:right;font-size:.88rem}
th{font-weight:600;color:var(--muted);font-size:.75rem;white-space:nowrap}th:first-child,td:first-child{text-align:left}
.empty{text-align:center;padding:22px 8px}.empty b{display:block;margin-bottom:4px}
form .f{margin:0 0 12px}label{display:block;font-size:.85rem;color:var(--muted);margin:0 0 4px}
input,textarea,select{font:inherit;width:100%;padding:9px 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
textarea{min-height:90px}.pair{display:grid;grid-template-columns:1.2fr 1fr;gap:8px;margin-bottom:8px}
.opt{display:flex;gap:8px;flex-wrap:wrap}.opt label{display:flex;align-items:center;gap:6px;margin:0;padding:8px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--fg)}
.opt input{width:auto}.err{background:var(--warn-bg);color:var(--warn-fg);border-radius:12px;padding:8px 12px;margin:0 0 12px}
.ok-msg{background:var(--soft);color:var(--accent-strong);border-radius:12px;padding:8px 12px;margin:0 0 12px;font-size:.88rem}
.ck{display:flex;gap:8px;padding:5px 0;border-top:1px dashed var(--line)}.ck:first-of-type{border-top:0}
.ck i{flex:none;font-style:normal;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:.8rem;font-weight:700;background:var(--line);color:var(--muted)}
.ck i.y{background:var(--accent);color:var(--on)}.ck i.n{background:var(--bad);color:var(--card)}
.inline{display:inline}.inline button{width:100%}
.mode{background:var(--warn-bg);color:var(--warn-fg);border-radius:12px;padding:6px 12px;font-size:.8rem;margin:0 0 10px}
nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:5;background:var(--card);border-top:1px solid var(--line);padding:6px 0 calc(6px + env(safe-area-inset-bottom))}
nav.tabs div{max-width:640px;margin:0 auto;display:flex}
nav.tabs a{flex:1;display:flex;flex-direction:column;align-items:center;font-size:.7rem;color:var(--muted);gap:1px}
nav.tabs a.on{color:var(--accent-strong);font-weight:600}
"""


def h(v) -> str:
    return html.escape("" if v is None else str(v))


def icon(name: str) -> str:
    return f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


def url(path: str, **params) -> str:
    p = {k: v for k, v in params.items() if v not in (None, "")}
    return path + ("?" + urlencode(p) if p else "")


def yen(v) -> str:
    return "—" if v is None else f"¥{int(v):,}"


def pct(v, digits=0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def day_label(d: str) -> str:
    try:
        x = date.fromisoformat(d)
    except (TypeError, ValueError):
        return h(d)
    return f"{x.month}/{x.day}（{WEEK[x.weekday()]}）"


def race_title(r: dict) -> str:
    return f"{r['venue']} {r['race_number']}R"


def race_sub(r: dict) -> str:
    bits = [r.get("post_time") and f"{r['post_time']} 発走",
            (r.get("surface") or "") + (f"{int(r['distance_m'])}m" if r.get("distance_m") else ""),
            r.get("going"), r.get("field_size") and f"{int(r['field_size'])}頭"]
    return "・".join(h(b) for b in bits if b)


def race_name(r: dict) -> str:
    n = r.get("race_name") or r.get("race_class") or ""
    return n.split(" ")[0] if isinstance(n, str) and n else ""


def conf_badge(c) -> str:
    c = c or "—"
    cls = "" if c == "A" else "b" if c == "B" else "c"
    return f'<span class="badge {cls}" title="自信度">{h(c)}</span>'


def marks_html(src, rk: str, p: dict, names: bool = False) -> str:
    out = []
    for i, (mk, n) in enumerate(marks_of(p)):
        nm = f' <span class="nm">{h(src.name(rk, n))}</span>' if names and src.name(rk, n) else ""
        out.append(f'<span class="mk m{i}">{mk} {n}{nm}</span>')
    return "".join(out) or '<span class="muted">印なし</span>'


def real_label(src) -> str:
    """本人の記録の呼び名。検証データで付けた記録は練習なので「実購入」と呼ばない。"""
    return "実購入" if src.mode == "live" else "練習の記録（検証データ）"


def money_text(st: dict) -> str:
    s_ = st["status"]
    if s_ == "settled":
        return f"払戻 {yen(st['returned'])}" + (f"（うち返還 {yen(st['refund'])}）" if st.get("refund") else "")
    if s_ == "refunded_void":
        return f"返還 {yen(st['returned'])}（" + ("レース取りやめ" if st.get("cancelled") else "レース不成立") + "）"
    text = SETTLE_LABEL.get(s_, "")
    if st.get("refund") and s_ in ("partial_result", "unsettled_no_payout"):
        text += f"・取消の返還 {yen(st['refund'])} は確定"
    return text


def empty(title: str, body: str, link: str = "") -> str:
    return f'<div class="card flat empty"><b>{h(title)}</b><span class="muted">{h(body)}</span>{link}</div>'


def layout(ctx, title: str, body: str, tab: str | None = None, back: str | None = None, top: str = "",
           refresh: bool = False) -> str:
    theme = ctx.get("theme", "auto")
    attrs = (f' data-theme="{h(theme)}"' if theme in ("light", "dark") else "") + (' data-size="large"' if ctx.get("size") == "large" else "")
    left = f'<a class="ib" href="{h(back)}" aria-label="戻る">{icon("back")}</a>' if back else ""
    mode = (f'<div class="mode">{h(MODES["check"])}を表示中。発走前の予想ではなく、画面の確認用だよ。'
            f'<a href="/settings"><u>切り替え</u></a></div>' if ctx.get("mode") == "check" else "")
    if ctx.get("stale"):   # 収集が台帳を書いている最中で、前に読んだデータを表示している
        mode += f'<div class="mode">{h(ctx["stale"])}（次の表示で読み直すよ）</div>'
    tabs = "".join(f'<a href="/{k}" class="{"on" if k == tab else ""}"{" aria-current=page" if k == tab else ""}>{icon(ic)}{lab}</a>'
                   for k, lab, ic in TABS)
    return (f'<!doctype html><html lang="ja"{attrs}><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            + (f'<meta http-equiv="refresh" content="{REFRESH_SEC}">' if refresh and ctx.get("refresh", "on") == "on" else "") +
            f'<meta name="theme-color" content="#639922"><title>{h(title)}｜三連複の確認</title><style>{CSS}</style></head>'
            f'<body><header class="top">{left}<span class="ttl">{h(top)}</span>'
            f'<a class="ib" href="/menu" aria-label="メニュー">{icon("menu")}</a></header>'
            f'<main>{mode}{body}</main><nav class="tabs" aria-label="タブ"><div>{tabs}</div></nav></body></html>')


REFRESH_SEC = 60


def auto_refresh(ctx, src) -> bool:
    return src.mode == "live"


def where_text(ctx) -> str:
    return "同じWi-FiのiPhoneからも、合言葉で開けるようにしているよ" if ctx.get("lan") else "このMacの中だけで動いていて、外には公開していないよ"


def seg(items, current, make) -> str:
    return '<div class="seg">' + "".join(
        f'<a href="{h(make(k))}" class="{"on" if k == current else ""}">{h(lab)}</a>' for k, lab in items) + "</div>"


def chips(items, current, make) -> str:
    return '<div class="chips">' + "".join(
        f'<a href="{h(make(k))}" class="{"on" if k == current else ""}">{h(lab)}</a>' for k, lab in items) + "</div>"


def link_list(items) -> str:
    rows = "".join(f'<a href="{h(u)}"><span>{h(t)}{"<br><span class=muted>" + h(s) + "</span>" if s else ""}</span>'
                   f'<span class="chev">›</span></a>' for t, s, u in items)
    return f'<div class="card list">{rows}</div>'


def default_day(src) -> str | None:
    days = src.days()
    if not days:
        return None
    if src.mode == "check":
        return days[-1]
    today = date.today().isoformat()
    future = [d for d in days if d >= today]
    return future[0] if future else days[-1]


# ======================= レース =======================

def _stale_banner(ctx, src) -> str:
    """収集が止まっていそうなら、一覧のいちばん上に出す（古い情報を最新と見せない）。"""
    if src.mode != "live":
        return ""
    health = ctx.get("health") or {}
    checked = health.get("checked_at")
    now = now_jst()
    soon = [r for r in src.races.values() if (post_datetime(r) or now) > now and (post_datetime(r) - now).days < 7]
    if not soon:
        return ""
    if not checked:
        return '<div class="err small">収集の記録（health.json）が見つからないよ。データの状態を確かめてね</div>'
    try:
        age = (now - datetime.fromisoformat(checked)).total_seconds() / 60
    except ValueError:
        return ""
    if age > 30:
        return (f'<div class="err small">収集の最終確認が{age:.0f}分前。止まっているかも。'
                f'表示は {checked[11:16]} 時点の情報だよ <a href="/status"><u>データの状態</u></a></div>')
    return ""


SOON_MIN = 60


def races_page(ctx, src, store, q) -> str:
    days = src.days()
    day = q.get("day") or default_day(src)
    tp = q.get("tp") if q.get("tp") in TIME_POINTS else None
    if tp is None:  # その日にある一番新しい時点（検証データは 24時間前・1時間前だけ）
        have = {t for r in src.races_on(day or "") for t in TIME_POINTS if src.pick(r["race_key"], "ml", t)[1]}
        tp = next((t for t in reversed(TIME_POINTS) if t in have), "24h")   # まだ何も無ければ、最初に出る24時間前
    if not day:
        body = "<h1>レース</h1><p class='lead'>予想を選ぶ、見送る、振り返る</p>" + _stale_banner(ctx, src) + empty(
            "まだレースが無いよ", "収集が動くと、ここに開催日のレースが並ぶよ。", '<p><a class="chip ok" href="/status">データの状態を見る</a></p>')
        return layout(ctx, "レース", body, "races")
    i = days.index(day) if day in days else 0
    prev_d = days[i - 1] if i > 0 else None
    next_d = days[i + 1] if i + 1 < len(days) else None
    venue = q.get("venue", "")
    races = src.races_on(day)
    venues = sorted({r["venue"] for r in races})
    if venue and venue not in venues:
        venue = ""
    shown = [r for r in races if not venue or r["venue"] == venue]
    entries = store.entries(data_mode=src.mode)
    bought = {e["race_key"] for e in entries if e["kind"] == "buy"}
    skipped_me = {e["race_key"] for e in entries if e["kind"] == "skip"}
    nav = (f'<div class="row" style="margin:0 0 10px"><a class="ib" href="{h(url("/races", day=prev_d, tp=tp))}" aria-label="前の日"'
           f'{" style=visibility:hidden" if not prev_d else ""}>‹</a><b>{day_label(day)}</b>'
           f'<a class="ib" href="{h(url("/races", day=next_d, tp=tp))}" aria-label="次の日"{" style=visibility:hidden" if not next_d else ""}>›</a></div>')
    body = ["<h1>レース</h1><p class='lead'>予想を選ぶ、見送る、振り返る</p>", _stale_banner(ctx, src), nav,
            seg([(t, TP_LABEL[t]) for t in TIME_POINTS], tp, lambda t: url("/races", day=day, tp=t, venue=venue))]
    if len(venues) > 1:
        body.append(chips([("", "すべて"), *[(v, v) for v in venues]], venue, lambda v: url("/races", day=day, tp=tp, venue=v, f=q.get("f"))))
    filter_at = len(body)
    cards, counts = [], Counter()
    flt = q.get("f") if q.get("f") in ("soon", "warn") and src.mode == "live" else ""
    alloc = MAIN_WALLET[src.mode] if src.mode == "live" else None
    now = now_jst()
    for r in shown:
        rk = r["race_key"]
        m, p = src.pick(rk, "ml", tp)
        res = src.results.get(rk)
        status = []
        a = allocation_of(p, alloc) if (p and alloc) else {}
        stakes = a.get("stakes") or {}
        if rk in bought:
            status.append('<span class="chip ok">購入記録あり</span>')
        elif rk in skipped_me:
            status.append('<span class="chip">見送り（自分）</span>')
        if p and alloc and is_skip(p, alloc):
            status.append('<span class="chip">見送り</span>')
            counts["skip"] += 1
        if res and res.get("label"):
            status.append(f'<span class="chip">結果 {h(res["label"])}</span>')
        warn = False
        if src.mode == "live":
            for cls, text in card_flags(p, stakes, r, bool(res and res.get("has_result")), now, src.names.get(rk),
                                        src.scratched_now.get(rk), src.card_checked_at.get(rk), tp,
                                        src.card_problem.get(rk)):
                status.append(f'<span class="chip {cls}">{h(text)}</span>')
                warn = warn or cls == "warn"
            counts["warn"] += warn
        post = post_datetime(r)
        soon = bool(post and not (res and res.get("has_result")) and 0 <= (post - now).total_seconds() <= SOON_MIN * 60)
        counts["soon"] += soon
        if (flt == "soon" and not soon) or (flt == "warn" and not warn):
            continue
        if p:
            counts["pred"] += 1
            counts["A"] += p.get("confidence") == "A"
            counts["bet"] += bool(stakes)
            total = sum(stakes.values())
            foot = (f"三連複 {len(stakes)}点・{yen(total)}" if total else
                    ALLOC_STATUS.get(a.get("status"), "") if alloc else f"確率の高い組 {h(p['trios'][0]['combo']) if p.get('trios') else '—'}")
            inner = (f'<div class="row"><div><b>{h(race_title(r))}</b> <span class="muted">{h(r.get("post_time") or "")}</span>'
                     f'<div style="font-size:1.05rem;font-weight:700">{h(race_name(r)) or "&nbsp;"}</div></div>{conf_badge(p.get("confidence"))}</div>'
                     f'<div>{marks_html(src, rk, p)}</div><div class="muted small" style="margin-top:6px">{h(foot)}</div>'
                     f'<div style="margin-top:2px">{"".join(status)}</div>')
            hot = " hot" if p.get("confidence") == "A" else ""
        else:
            earlier = src.latest_tp(rk)
            inner = (f'<div class="row"><div><b>{h(race_title(r))}</b> <span class="muted">{h(r.get("post_time") or "")}</span>'
                     f'<div style="font-weight:700">{h(race_name(r)) or "&nbsp;"}</div></div>{conf_badge(None)}</div>'
                     f'<div class="muted small">{TP_LABEL[tp]}の予想はまだ無いよ'
                     f'{"（" + TP_LABEL[earlier] + "はある）" if earlier else ""}</div><div>{"".join(status)}</div>')
            hot = ""
        cards.append(f'<a class="card{hot}" style="display:block" href="{h(url("/race", rk=rk, tp=tp))}">{inner}</a>')
    if src.mode == "live" and shown:
        body.insert(filter_at, chips([("", "すべて"), ("soon", f"発走{SOON_MIN}分以内 {counts['soon']}"),
                                      ("warn", f"要確認 {counts['warn']}")], flt,
                                     lambda k: url("/races", day=day, tp=tp, venue=venue, f=k)))
    if shown:
        bits = [f"{len(shown)}R", f"予想 {counts['pred']}", f"自信度A {counts['A']}"]
        if alloc:
            bits += [f"買い目あり {counts['bet']}", f"見送り {counts['skip']}"]
        if counts["warn"]:
            bits.append(f"警告 {counts['warn']}")
        body.append(f'<p class="muted small" style="margin:-4px 0 10px">{h("・".join(bits))}'
                    + (f'<br>読み込み {datetime.fromtimestamp(src.loaded_at):%H:%M}・1分ごとに読み直すよ' if auto_refresh(ctx, src)
                       and ctx.get("refresh", "on") == "on" else "") + '</p>')
    body.append("".join(cards) or (empty("あてはまるレースは無いよ", "「すべて」に戻すと全部見られるよ。") if flt
                                   else empty("この日のレースは無いよ", "")))
    return layout(ctx, "レース", "".join(body), "races", refresh=auto_refresh(ctx, src))


def _race_header(src, r, back_label="") -> str:
    name = race_name(r)
    return (f'<p class="muted" style="margin:0">{h(race_title(r))}{(" ・ " + day_label(r["race_date"])) if r.get("race_date") else ""}</p>'
            f'<h1>{h(name) if name else h(race_title(r))}</h1><p class="lead">{race_sub(r) or "&nbsp;"}</p>')


def _facts(src, rk, m, p, tp) -> str:
    """根拠のカード。事実・推論・注意を分ける。"""
    lines = []
    inp = p.get("inputs") or {}
    if inp.get("card_fetched_at"):
        lines.append(("f", "事実", f"出馬表の取得 {inp['card_fetched_at'][11:16]}"))
    if inp.get("scratched_known"):
        lines.append(("f", "事実", "取消・除外：" + "、".join(f"{n}番" for n in inp["scratched_known"])))
    gate = p.get("odds_gate") or {}
    if gate.get("odds_time"):
        lines.append(("f", "事実", f"三連複オッズ {gate['odds_time']} 発表・取得 {str(gate.get('fetched_at', ''))[11:16]}"))
    if probs_complete(p):
        lines.append(("g", "推論", f"印は{METHOD_LABEL.get(m, m)}の3着内確率の順。自信度 {p.get('confidence') or '—'}"))
    elif horse_probs(p):
        lines.append(("w", "注意", "確率は上位の組だけから足した目安（全部の組はそろっていない）。印の順も目安"))
    else:
        lines.append(("w", "注意", "確率が無く、順位だけの予想。印は順位の上から"))
    view = p.get("rule_view") or {}
    if view.get("disagreement"):
        lines.append(("g", "推論", f"ルールとの食い違い：{view['disagreement']}（配分には使わない）"))
    for reason in gate.get("reasons") or []:
        lines.append(("w", "注意", reason))
    if p.get("uses_post_race_odds"):
        lines.append(("w", "注意", "発走後の確定オッズを使う比較用。予想には使えない"))
    if src.mode == "check":
        lines.append(("w", "注意", "検証データ：過去のレースの予想。発走前のオッズは無いので配分なし"))
    rows = "".join(f'<div style="padding:3px 0"><span class="tag {c}">{h(t)}</span>{h(x)}</div>' for c, t, x in lines[:4])
    more = ""
    if len(lines) > 4:
        more = f'<p class="muted small" style="margin:6px 0 0">ほか{len(lines) - 4}件は「時点の変化」で見られるよ</p>'
    return f'<div class="card"><h2>予想の根拠</h2>{rows}{more}</div>'


def race_page(ctx, src, store, q) -> str:
    rk = q.get("rk", "")
    r = src.races.get(rk)
    if not r:
        return layout(ctx, "見つからない", empty("レースが見つからないよ", "一覧から選び直してね。"), "races", "/races")
    tab = q.get("m") if q.get("m") in METHOD_TABS else "ml"
    tp = q.get("tp") if q.get("tp") in TIME_POINTS else (src.latest_tp(rk, tab) or "15m")
    alloc = q.get("a") if q.get("a") in ALLOC_LABEL else MAIN_WALLET["live"]
    back = url("/races", day=r["race_date"], tp=tp)
    flash = {"skip": "見送りを記録したよ（履歴でいつでも直せる）"}.get(q.get("saved", ""), "")
    body = [_race_header(src, r), f'<div class="ok-msg">{h(flash)}</div>' if flash else "",
            chips([(k, v[0]) for k, v in METHOD_TABS.items()], tab, lambda k: url("/race", rk=rk, m=k, tp=tp, a=alloc)),
            seg([(t, TP_LABEL[t]) for t in TIME_POINTS], tp, lambda t: url("/race", rk=rk, m=tab, tp=t, a=alloc))]
    m, p = src.pick(rk, tab, tp)
    if not p:
        have = [t for t in TIME_POINTS if src.pick(rk, tab, t)[1]]
        hint = "、".join(TP_LABEL[t] for t in have)
        msg = {"mam": "まむの予想はまだ届いていないよ。", "int": "統合は1時間前・15分前にだけ作るよ。"}.get(tab, "")
        body.append(empty(f"{METHOD_TABS[tab][0]}の{TP_LABEL[tp]}の予想は無いよ",
                          msg + (f"ある時点：{hint}" if hint else "この方式の予想はまだ無いよ。")))
        body.append(link_list([("方式くらべ", "どの方式が何を選んだか", url("/race/compare", rk=rk, tp=tp))]))
        return layout(ctx, race_title(r), "".join(body), "races", back, race_title(r), refresh=auto_refresh(ctx, src))
    timing, ttext = material_timing(p, r, tp)
    if timing == "early":
        body.append(f'<div class="err small">{TP_LABEL[tp]}の材料がそろう前に作った、早めの参考予想だよ。{h(ttext)}</div>')
    elif timing == "late" and src.mode == "live":
        body.append(f'<div class="err small">{TP_LABEL[tp]}の締切より後の材料で作った予想だよ。{h(ttext)}</div>')
    body.append(f'<div class="card"><div class="row"><h2>印</h2>{conf_badge(p.get("confidence"))}</div>'
                f'<div>{marks_html(src, rk, p, names=True)}</div>'
                f'<a class="muted small" style="display:block;margin-top:8px" href="{h(url("/race/horses", rk=rk, m=tab, tp=tp))}">全頭の評価を見る ›</a></div>')
    a = allocation_of(p, alloc)
    stakes = a.get("stakes") or {}
    items_ = pre_publish_check(p, stakes, r, src.names.get(rk), src.scratched_now.get(rk), src.card_checked_at.get(rk), tp,
                               src.card_problem.get(rk))
    ok_n, ng_n, na_n = check_summary(items_)
    unk_n = unknown_required(items_) if stakes else 0
    check_link = (f'<a class="small" style="display:block;margin-top:8px" href="{h(url("/race/check", rk=rk, m=tab, tp=tp, a=alloc))}">'
                  + (f'<span class="chip warn">チェック × {ng_n}件</span>' if ng_n else
                     f'<span class="chip warn">確認できない {unk_n}件</span><span class="muted"> ✓{ok_n}件</span>' if unk_n else
                     f'<span class="chip ok">チェック ✓ {ok_n}件</span>' + (f'<span class="muted"> —{na_n}件</span>' if na_n else "")
                     if ok_n else '<span class="chip">チェック対象なし</span>')
                  + '<span class="muted"> 偏り・変わる条件も ›</span></a>')
    pills = ""
    if src.mode == "live" and m not in ("rule",):
        pills = chips(list(ALLOC_LABEL.items()), alloc, lambda k: url("/race", rk=rk, m=tab, tp=tp, a=k))
    if stakes:
        rows = sorted(stakes.items(), key=lambda x: -x[1])
        bets = "".join(f'<div class="bet"><span>{h(c)}</span><span>{yen(v)}</span></div>' for c, v in rows[:5])
        if len(rows) > 5:
            bets += f'<div class="bet muted"><span>ほか{len(rows) - 5}点</span><span>{yen(sum(v for _, v in rows[5:]))}</span></div>'
        bets += f'<div class="bet sum"><span>合計 {len(rows)}点</span><span>{yen(sum(stakes.values()))}</span></div>'
        body.append(f'<div class="card"><div class="row"><h2>三連複の買い目</h2><span class="muted small">1レース上限 ¥10,000</span></div>{pills}{bets}{check_link}</div>')
    else:
        top = [t for t in p.get("trios", []) if t.get("p") is not None][:3]
        rows = "".join(f'<div class="bet"><span>{h(t["combo"])}</span><span class="muted">{t["p"] * 100:.1f}%</span></div>' for t in top)
        body.append(f'<div class="card"><h2>三連複の買い目</h2>{pills}<p class="muted small" style="margin:0 0 4px">'
                    f'{h(ALLOC_STATUS.get(a.get("status"), a.get("reason") or "配分なし"))}</p>'
                    f'<p class="muted small" style="margin:6px 0 0">確率の高い組（参考）</p>{rows or "<span class=muted>—</span>"}{check_link}</div>')
    body.append(_facts(src, rk, m, p, tp))
    res = src.results.get(rk)
    items = [("方式くらべ", "一致と不一致", url("/race/compare", rk=rk, tp=tp)),
             ("時点の変化", "24時間前 → 1時間前 → 15分前", url("/race/timeline", rk=rk, m=tab))]
    if res and res.get("label"):
        wins = set(res.get("payouts") or {}) or {res["actual"]}
        hit = bool(wins & set(stakes)) if stakes else any(t["combo"] in wins for t in p.get("trios", [])[:10])
        pay = "・".join(yen(v) for v in (res.get("payouts") or {}).values())
        items.append((f"結果 {res['label']}", "全額返還" if res.get("cancelled") else
                      ("当たり" if hit else "はずれ") + (f"・払戻 {pay}" if pay else "・払戻はまだ分からない"),
                      url("/history/detail", rk=rk)))
    body.append(link_list(items))
    skip = (f'<form method="post" action="/buy" class="inline" style="flex:1"><input type="hidden" name="token" value="{h(ctx["token"])}">'
            f'<input type="hidden" name="rk" value="{h(rk)}"><input type="hidden" name="kind" value="skip">'
            f'<input type="hidden" name="m" value="{h(m)}"><input type="hidden" name="tp" value="{h(tp)}">'
            f'<input type="hidden" name="back" value="race"><input type="hidden" name="tab" value="{h(tab)}">'
            f'<input type="hidden" name="a" value="{h(alloc)}"><input type="hidden" name="fm" value="{h(src.mode)}">'
            f'<button class="btn" type="submit">見送る（1タップ）</button></form>')
    body.append(f'<div class="btns">{skip}<a class="btn pri" href="{h(url("/buy/new", rk=rk, m=m, tp=tp, a=alloc))}">購入を記録</a></div>')
    return layout(ctx, race_title(r), "".join(body), "races", back, race_title(r), refresh=auto_refresh(ctx, src) and not q.get("saved"))


def horses_page(ctx, src, store, q) -> str:
    rk = q.get("rk", "")
    r = src.races.get(rk)
    tab = q.get("m") if q.get("m") in METHOD_TABS else "ml"
    tp = q.get("tp") if q.get("tp") in TIME_POINTS else "15m"
    m, p = src.pick(rk, tab, tp) if r else (None, None)
    back = url("/race", rk=rk, m=tab, tp=tp)
    if not p:
        return layout(ctx, "全頭の評価", empty("予想が無いよ", ""), "races", back, "全頭の評価")
    hp = horse_probs(p)
    marks = {n: mk for mk, n in marks_of(p)}
    groups = {int(x["n"]): x for x in p.get("horses", [])}
    rule_p = p if m == "rule" else src.pick(rk, "rule", tp)[1]   # 一言はルールの8項目から作る
    comments = {int(x["n"]): horse_comment(x) for x in (rule_p or {}).get("horses", []) if x.get("groups")}
    rows = []
    ranked_only = not hp
    if ranked_only:   # 確率が無く順位だけ（まむ）：順位の順に全頭を並べる
        order = sorted(groups, key=lambda n: (groups[n].get("rank") is None, groups[n].get("rank") or 0, n))
    else:
        order = sorted(hp, key=lambda k: (-hp[k], k))
    for n in order:
        g = groups.get(n, {})
        extra = ""
        if "coverage" in g:
            extra = f'<td>{g["score"] if g.get("score") is not None else "—"}</td>'
        cm = f'<br><span class="small"><span class="tag g">推論</span>{h(comments[n])}</span>' if comments.get(n) else ""
        val = (f'{g["rank"]}位' if g.get("rank") else "—") if ranked_only else f"{hp[n] * 100:.1f}%"
        rows.append(f'<tr><td>{h(marks.get(n, ""))} {n} <span class="muted small">{h(src.name(rk, n))}</span>{cm}</td>'
                    f'<td>{val}</td>{extra}</tr>')
    head = "<th>総合（10段階）</th>" if any("coverage" in g for g in groups.values()) else ""
    calibrated = probs_complete(p) and m in ("ml_lgbm", "ml_market", "rule", "rule_v11", "rule_v01", "base_logit")
    note = ("順位だけの予想なので、確率は無いよ。" if ranked_only else
            "確率は過去のデータで較正した推定で、当たりの保証ではないよ。" if calibrated else
            "確率は較正していない目安（上位の組だけから足したものや、オッズから出したもの）。当たりの保証ではないよ。")
    body = (f'<p class="muted" style="margin:0">{h(race_title(r))}・{TP_LABEL[tp]}</p><h1>全頭の評価</h1>'
            f'<p class="lead">{h(METHOD_LABEL.get(m, m))}。{"順位の順" if ranked_only else "3着内に入る確率の高い順"}</p>'
            f'<div class="card"><table><tr><th>馬番・馬名</th><th>{"順位" if ranked_only else "3着内"}</th>{head}</tr>{"".join(rows)}</table></div>'
            f'<p class="muted small">{note}'
            f'{"ルールの総合は、評価できた項目だけの平均。" if head else ""}'
            f'{"一言はルールの8項目（10段階）から：◎は7.5以上、△は3以下。" if comments else ""}</p>')
    return layout(ctx, "全頭の評価", body, "races", back, "全頭の評価")


def compare_page(ctx, src, store, q) -> str:
    rk = q.get("rk", "")
    r = src.races.get(rk)
    tp = q.get("tp") if q.get("tp") in TIME_POINTS else "15m"
    back = url("/race", rk=rk, tp=tp)
    if not r:
        return layout(ctx, "方式くらべ", empty("レースが見つからないよ", ""), "races", "/races")
    preds = {m: p for (m, t), p in src.preds.get(rk, {}).items() if t == tp}
    body = [f'<p class="muted" style="margin:0">{h(race_title(r))}</p><h1>方式くらべ</h1><p class="lead">同じ時点で、方式ごとの上位3頭</p>',
            seg([(t, TP_LABEL[t]) for t in TIME_POINTS], tp, lambda t: url("/race/compare", rk=rk, tp=t))]
    if not preds:
        body.append(empty(f"{TP_LABEL[tp]}の予想は無いよ", "ほかの時点を選んでね。"))
        return layout(ctx, "方式くらべ", "".join(body), "races", back, "方式くらべ")
    order = [m for m in METHOD_LABEL if m in preds]
    tops = {m: [n for _, n in marks_of(preds[m])] for m in order}
    # 一致・不一致は、画面のタブが実際に出している方式だけで見る（統合なら候補のうち出ているもの。比較版は下に並べるだけ）
    tab_methods = {src.pick(rk, k, tp)[0] for k in METHOD_TABS} - {None}
    main = [m for m in order if m in tab_methods]
    count = Counter(n for m in main for n in tops[m])
    if len(main) < 2:
        summary = ('<div style="padding:3px 0" class="muted small">比べられる方式が'
                   + ("1つだけ" if main else "無い") + 'なので、一致・割れは出さないよ</div>')
    else:
        common = sorted(n for n, c in count.items() if c == len(main))
        some = sorted(n for n, c in count.items() if 1 < c < len(main))
        split = sorted(n for n, c in count.items() if c == 1)
        summary = (f'<div style="padding:3px 0"><span class="tag f">一致</span>'
                   f'{"、".join(f"{n}番" for n in common) + f" は{len(main)}方式すべてが上位3頭" if common else "全方式で共通の馬はいない"}</div>'
                   + (f'<div style="padding:3px 0"><span class="tag g">一部</span>' + "、".join(
                       f"{n}番（{count[n]}/{len(main)}）" for n in some) + ' は一部の方式だけが選んだ</div>' if some else "")
                   + f'<div style="padding:3px 0"><span class="tag g">割れた</span>'
                   f'{"、".join(f"{n}番" for n in split) + " は1つの方式だけが選んだ" if split else "なし"}</div>')
    body.append(f'<div class="card"><h2>一致と不一致</h2>{summary}<p class="muted small" style="margin:6px 0 0">'
                f'比べたのは{"・".join(METHOD_LABEL[m] for m in main) or "—"}。比較版は下に並べるだけ</p></div>')
    rows = "".join(f'<div style="display:block"><div class="row"><span>{h(METHOD_LABEL.get(m, m))}</span>{conf_badge(preds[m].get("confidence"))}</div>'
                   f'<div>{marks_html(src, rk, preds[m])}</div>{_compare_detail(preds[m])}</div>' for m in order)
    body.append(f'<div class="card list">{rows}</div>'
                '<p class="muted small">組＝確率の高い三連複（上位3つ）。理由は各方式が残した説明の先頭。どれも推論だよ</p>')
    return layout(ctx, "方式くらべ", "".join(body), "races", back, "方式くらべ")


def _compare_detail(p: dict) -> str:
    combos = [t for t in p.get("trios") or [] if t.get("combo")][:3]
    ctext = "、".join(t["combo"] + (f"（{t['p'] * 100:.1f}%）" if t.get("p") is not None else "") for t in combos) or "—"
    reasons = [*(p.get("reasons") or []), *(p.get("notes") or [])]
    view = p.get("rule_view") or {}
    if view.get("disagreement"):
        reasons.insert(0, f"ルールとの食い違い：{view['disagreement']}")
    gap = ""
    hp = horse_probs(p)
    order = sorted(hp, key=lambda n: (-hp[n], n))
    if len(order) >= 4 and probs_complete(p):
        gap = f"★と4番手の差 {(hp[order[2]] - hp[order[3]]) * 100:.1f}ポイント"
    bits = [f'<div class="muted small">組：{h(ctext)}</div>']
    if gap:
        bits.append(f'<div class="muted small">{h(gap)}</div>')
    if reasons:
        bits.append(f'<div class="muted small">理由：{h(str(reasons[0])[:80])}</div>')
    return "".join(bits)


def _change_lines(prev: dict | None, cur: dict, alloc: str | None = None) -> list[str]:
    if prev is None:
        return ["最初の予想"]
    out = []
    pm, cm = dict(marks_of(prev)), dict(marks_of(cur))
    for mk in ("◎", "○", "★"):
        if pm.get(mk) != cm.get(mk):
            out.append(f"{mk}が {pm.get(mk, '—')}番 → {cm.get(mk, '—')}番")
    if prev.get("confidence") != cur.get("confidence"):
        out.append(f"自信度 {prev.get('confidence') or '—'} → {cur.get('confidence') or '—'}")
    new_scr = set((cur.get("inputs") or {}).get("scratched_known") or []) - set((prev.get("inputs") or {}).get("scratched_known") or [])
    if new_scr:
        out.append("取消・除外が分かった：" + "、".join(f"{n}番" for n in sorted(new_scr)))
    if (cur.get("inputs") or {}).get("win_odds_ok") and not (prev.get("inputs") or {}).get("win_odds_ok"):
        out.append("単勝オッズが入った")
    if alloc:
        ps, cs = allocation_of(prev, alloc).get("stakes") or {}, allocation_of(cur, alloc).get("stakes") or {}
        if ps != cs:
            if set(ps) != set(cs):
                out.append(f"買い目が変わった：{len(ps)}点 {yen(sum(ps.values()))} → {len(cs)}点 {yen(sum(cs.values()))}")
            else:
                out.append(f"金額だけ変わった：{yen(sum(ps.values()))} → {yen(sum(cs.values()))}")
    pt = [t.get("combo") for t in prev.get("trios") or []][:3]
    ct = [t.get("combo") for t in cur.get("trios") or []][:3]
    if pt and ct and pt != ct and not any(x.startswith(("◎", "○", "★")) for x in out):
        out.append("確率の高い組の顔ぶれ・順が変わった")
    return out or ["変化なし（印・自信度・買い目・金額・取消・オッズ）"]


def timeline_page(ctx, src, store, q) -> str:
    rk = q.get("rk", "")
    r = src.races.get(rk)
    tab = q.get("m") if q.get("m") in METHOD_TABS else "ml"
    back = url("/race", rk=rk, m=tab)
    if not r:
        return layout(ctx, "時点の変化", empty("レースが見つからないよ", ""), "races", "/races")
    alloc = MAIN_WALLET["live"]
    body = [f'<p class="muted" style="margin:0">{h(race_title(r))}</p><h1>時点の変化</h1><p class="lead">何がいつ変わったか</p>',
            chips([(k, v[0]) for k, v in METHOD_TABS.items()], tab, lambda k: url("/race/timeline", rk=rk, m=k))]
    prev, cards = None, []
    for tp in TIME_POINTS:
        m, p = src.pick(rk, tab, tp)
        if not p:
            cards.append(f'<div class="card flat"><span class="chip">{TP_LABEL[tp]}</span> <span class="muted small">予想なし</span></div>')
            continue
        a = allocation_of(p, alloc) if src.mode == "live" else {}
        stakes = a.get("stakes") or {}
        money = f"{len(stakes)}点・配分 {yen(sum(stakes.values()))}" if stakes else ALLOC_STATUS.get(a.get("status"), "配分なし")
        changes = "".join(f'<div class="small">{h(x)}</div>' for x in _change_lines(prev, p, alloc if src.mode == "live" else None))
        timing, ttext = material_timing(p, r, tp) if src.mode == "live" else ("", "")
        tchip = {"early": '<span class="chip warn">早めの参考予想</span>', "late": '<span class="chip warn">締切後の材料</span>',
                 "unknown": '<span class="chip">時点を確認できない</span>'}.get(timing, "")
        cards.append(f'<div class="card{" hot" if tp == "15m" else ""}"><div class="row"><span class="chip ok">{TP_LABEL[tp]}</span>'
                     f'{conf_badge(p.get("confidence"))}{tchip}</div>'
                     + (f'<div class="muted small">{h(ttext)}</div>' if ttext else "")
                     + f'<div>{marks_html(src, rk, p)}</div>'
                     f'<div class="muted small" style="margin:4px 0">{h(money)}</div><div class="tl muted">{changes}</div></div>')
        prev = p
    body.append("".join(cards))
    return layout(ctx, "時点の変化", "".join(body), "races", back, "時点の変化")


def check_page(ctx, src, store, q) -> str:
    """買い目のチェック：公開前チェック・偏り・予想が変わる条件（3枚まで）。"""
    rk = q.get("rk", "")
    r = src.races.get(rk)
    tab = q.get("m") if q.get("m") in METHOD_TABS else "ml"
    tp = q.get("tp") if q.get("tp") in TIME_POINTS else (src.latest_tp(rk, tab) if r else None) or "15m"
    alloc = q.get("a") if q.get("a") in ALLOC_LABEL else MAIN_WALLET["live"]
    back = url("/race", rk=rk, m=tab, tp=tp, a=alloc)
    m, p = src.pick(rk, tab, tp) if r else (None, None)
    if not p:
        return layout(ctx, "買い目のチェック", empty("予想が無いよ", "レースの詳細から開き直してね。"), "races", back, "買い目のチェック")
    stakes = allocation_of(p, alloc).get("stakes") or {}
    items = pre_publish_check(p, stakes, r, src.names.get(rk), src.scratched_now.get(rk), src.card_checked_at.get(rk), tp,
                               src.card_problem.get(rk))
    ok_n, ng_n, na_n = check_summary(items)
    unk_n = unknown_required(items) if stakes else 0
    rows = "".join(f'<div class="ck"><i class="{"y" if i["ok"] else "n" if i["ok"] is False else ""}">'
                   f'{"✓" if i["ok"] else "×" if i["ok"] is False else "—"}</i><div><div class="small">{h(i["label"])}</div>'
                   f'<div class="muted small">{h(i["detail"])}</div></div></div>' for i in items)
    body = [f'<p class="muted" style="margin:0">{h(race_title(r))}・{TP_LABEL[tp]}・{h(METHOD_LABEL.get(m, m))}</p>'
            f'<h1>買い目のチェック</h1><p class="lead">買う前に、おかしな所が無いか</p>',
            f'<div class="card"><div class="row"><h2>公開前チェック</h2>'
            f'<span class="chip {"warn" if ng_n or unk_n else "ok" if ok_n else ""}">'
            f'{"× " + str(ng_n) + "件" if ng_n else "確認できない " + str(unk_n) + "件" if unk_n else "✓ " + str(ok_n) + "件" if ok_n else "対象なし"}</span></div>{rows}'
            f'<p class="muted small" style="margin:6px 0 0">「—」は判定できない・対象が無い項目（{na_n}件）。配分：{h(ALLOC_LABEL.get(alloc, alloc))}</p></div>']
    bias = bias_check(p, stakes)
    tag = {"推論": "g", "注意": "w", "事実": "f"}
    if bias:
        body.append('<div class="card"><h2>買い目の偏り</h2>' + "".join(
            f'<div style="padding:3px 0" class="small"><span class="tag {tag[t]}">{h(t)}</span>{h(x)}</div>' for t, x in bias) + "</div>")
    else:
        body.append('<div class="card"><h2>買い目の偏り</h2><p class="muted small" style="margin:0">買い目が無いので見ていないよ</p></div>')
    cond = change_conditions(p, tp, r)
    body.append('<div class="card"><h2>予想が変わる条件</h2>' + "".join(
        f'<div style="padding:3px 0" class="small"><span class="tag {tag[t]}">{h(t)}</span>{h(x)}</div>' for t, x in cond) + "</div>")
    return layout(ctx, "買い目のチェック", "".join(body), "races", back, "買い目のチェック")


# ======================= 購入記録 =======================

def buy_form(ctx, src, store, q, error: str = "", form: dict | None = None) -> str:
    rk = q.get("rk", "")
    r = src.races.get(rk)
    entry = store.entry(q["entry"]) if q.get("entry") else None
    if entry:
        rk = entry["race_key"]
        r = src.races.get(rk) or {"race_key": rk, "venue": rk.split("|")[1], "race_number": rk.split("|")[2], "race_date": rk.split("|")[0]}
    if not r:
        return layout(ctx, "購入を記録", empty("レースが見つからないよ", ""), "history", "/races")
    kind = (form or {}).get("kind") or q.get("kind") or (entry["kind"] if entry else "buy")
    m, tp = q.get("m") or (entry or {}).get("method"), q.get("tp") or (entry or {}).get("time_point")
    rows: list[tuple[str, str]] = []
    if form:
        rows = list(form.get("rows", []))
    elif entry:
        rows = [(c, str(v)) for c, v in entry["stakes"].items()]
    elif kind == "buy" and m and tp:
        p = src.preds.get(rk, {}).get((m, tp))
        if p:
            st = allocation_of(p, q.get("a") or MAIN_WALLET["live"]).get("stakes") or {}
            rows = [(c, str(v)) for c, v in sorted(st.items(), key=lambda x: -x[1])]
    rows = rows + [("", "")] * max(2, 5 - len(rows)) if kind == "buy" else []
    memo = (form or {}).get("memo", entry["memo"] if entry else "")
    # フォームを開いたときのモード。保存のときに表示のモード（Cookie）と違えば、どちらに入れるか確認を出す
    mode = entry["data_mode"] if entry else ((form or {}).get("fm") if (form or {}).get("fm") in MODES else src.mode)
    who = "実購入" if mode == "live" else "練習（検証データ）"
    title = (f"見送りを記録（{who}）" if kind == "skip" else
             (f"{who}の記録を直す" if entry else ("購入を記録" if mode == "live" else "練習の記録を付ける")))
    back = url("/history/detail", rk=rk) if entry else url("/race", rk=rk, tp=tp)
    fields = "".join(f'<div class="pair"><input name="combo" inputmode="numeric" placeholder="3-7-11" value="{h(c)}" aria-label="組">'
                     f'<input name="yen" inputmode="numeric" placeholder="100円単位" value="{h(v)}" aria-label="金額（円）"></div>'
                     for c, v in rows)
    body = [f'<p class="muted" style="margin:0">{h(race_title(r))}・{day_label(r.get("race_date"))}</p><h1>{h(title)}</h1>',
            '<p class="lead">買うのはアプリの外で。ここは記録だけ（仮想の財布とは別に集計するよ）</p>',
            f'<div class="err">{h(error)}</div>' if error else "",
            f'<form method="post" action="/buy"><input type="hidden" name="token" value="{h(ctx["token"])}">'
            f'<input type="hidden" name="rk" value="{h(rk)}"><input type="hidden" name="kind" value="{h(kind)}">'
            f'<input type="hidden" name="m" value="{h(m)}"><input type="hidden" name="tp" value="{h(tp)}">'
            f'<input type="hidden" name="entry" value="{h(entry["entry_id"] if entry else "")}">'
            f'<input type="hidden" name="fm" value="{h(mode)}">']
    if (form or {}).get("confirm"):
        now_who = "実購入" if ctx["mode"] == "live" else "練習（検証データ）"
        body.append(f'<div class="card" style="border-color:var(--warn,#c60)"><h2>どちらに記録する？</h2>'
                    f'<p class="small">このフォームは「{h(who)}」で開いたけど、いまの表示は「{h(now_who)}」になっているよ'
                    '（別のタブで切り替えたかも）。入れる先を選んでね。</p><div class="btns">'
                    f'<button class="btn" type="submit" name="as_mode" value="check">練習（検証データ）に記録</button>'
                    f'<button class="btn" type="submit" name="as_mode" value="live">実購入に記録</button></div></div>')
    if kind == "buy":
        body.append(f'<div class="card"><h2>買った組と金額</h2><p class="muted small" style="margin:0 0 8px">三連複なので順番はどれでもOK。空の行は無視するよ</p>{fields}</div>')
    body.append(f'<div class="card"><h2>メモ</h2><textarea name="memo" placeholder="直前の人気の変化を見て決めた">{h(memo)}</textarea></div>'
                f'<div class="btns"><a class="btn" href="{h(back)}">やめる</a><button class="btn pri" type="submit">記録する</button></div></form>')
    if entry:
        body.append(f'<form method="post" action="/buy/void"><input type="hidden" name="token" value="{h(ctx["token"])}">'
                    f'<input type="hidden" name="entry" value="{h(entry["entry_id"])}">'
                    f'<p class="muted small">記録は消さずに「取り消し」の行を足すよ。<button class="chip warn" type="submit" style="cursor:pointer">この記録を取り消す</button></p></form>')
    return layout(ctx, title, "".join(body), "history", back, title)


# ======================= 履歴 =======================

def _history_keys(src, store, f: str) -> list[str]:
    entries = store.entries(data_mode=src.mode)
    buy = {e["race_key"] for e in entries if e["kind"] == "buy"}
    skip_user = {e["race_key"] for e in entries if e["kind"] == "skip"}
    alloc = MAIN_WALLET["live"]
    skip_pred = set()
    if src.mode == "live":
        for rk in src.preds:
            tp = src.latest_tp(rk)
            p = src.pick(rk, "ml", tp)[1] if tp else None
            if p and is_skip(p, alloc):
                skip_pred.add(rk)
    today = date.today().isoformat()
    if f == "buy":
        keys = buy
    elif f == "skip":
        keys = skip_user | skip_pred
    elif f == "2w":   # 予想が無いレースも、開催のあったレースは全部出す
        keys = set(src.races) | src.past_keys | set(src.preds) | buy | skip_user | store.noted_races()
        start = (date.fromisoformat(today) - timedelta(days=13)).isoformat()
        keys = {k for k in keys if start <= k.split("|")[0] <= today}
    else:
        keys = set(src.preds) | buy | skip_user | store.noted_races()
        if src.mode == "live":
            keys = {k for k in keys if k.split("|")[0] <= today}
    return sorted(keys, key=lambda k: race_sort_key(src, k), reverse=True)


def history_page(ctx, src, store, q) -> str:
    f = q.get("f") if q.get("f") in ("all", "2w", "buy", "skip") else "all"
    page = max(1, int(q.get("page", "1") or 1)) if str(q.get("page", "1")).isdigit() else 1
    keys = _history_keys(src, store, f)
    per = 15
    shown = keys[(page - 1) * per: page * per]
    body = ["<h1>履歴</h1><p class='lead'>予想の変化と判断を残す</p>",
            seg([("all", "すべて"), ("2w", "2週間"), ("buy", "購入記録"), ("skip", "見送り")], f, lambda k: url("/history", f=k))]
    if not keys:
        msg = {"buy": "購入を記録すると、ここに並ぶよ。", "skip": "見送ったレースがここに並ぶよ。",
               "2w": "今日までの14日間に、予想や記録のあるレースは無いよ。"}.get(f, "予想が保存されると、ここに並ぶよ。")
        body.append(empty("まだ0件だよ", msg))
        return layout(ctx, "履歴", "".join(body), "history")
    entries = defaultdict(list)
    for e in store.entries(data_mode=src.mode):
        entries[e["race_key"]].append(e)
    last_day = None
    for rk in shown:
        r = src.races.get(rk) or {"race_key": rk, "venue": rk.split("|")[1], "race_number": rk.split("|")[2], "race_date": rk.split("|")[0]}
        if r["race_date"] != last_day:
            body.append(f'<h2 style="margin-top:6px">{day_label(r["race_date"])}</h2>')
            last_day = r["race_date"]
        es = entries.get(rk, [])
        buys = [e for e in es if e["kind"] == "buy"]
        chips_ = []
        if buys:
            chips_.append(f'<span class="chip ok">購入記録あり {yen(sum(e["total"] for e in buys))}</span>')
        elif any(e["kind"] == "skip" for e in es):
            chips_.append('<span class="chip">見送り（自分）</span>')
        else:
            chips_.append('<span class="chip">未購入</span>')
        tp = src.latest_tp(rk)
        p = src.pick(rk, "ml", tp)[1] if tp else None
        dots = "".join(f'<span class="{"on" if src.pick(rk, "ml", t)[1] else ""}">{TP_LABEL[t]}</span>' for t in TIME_POINTS)
        note = store.note(rk)
        small_title = f'<span class="muted small">{h(race_title(r))}</span>' if race_name(r) else ""
        note_line = f'<div class="muted small">メモ：{h(note.splitlines()[0][:40])}</div>' if note else ""
        body.append(f'<a class="card" style="display:block" href="{h(url("/history/detail", rk=rk))}">'
                    f'<div class="row"><div>{small_title}'
                    f'<div style="font-weight:700">{h(race_name(r)) or h(race_title(r))}</div></div>{conf_badge(p.get("confidence") if p else None)}</div>'
                    f'<div>{"".join(chips_)}</div><div class="dots">{dots}</div>{note_line}</a>')
    pages = (len(keys) + per - 1) // per
    if pages > 1:
        body.append(f'<div class="row muted small"><a class="chip" href="{h(url("/history", f=f, page=page - 1))}"'
                    f'{" style=visibility:hidden" if page <= 1 else ""}>‹ 新しい方</a><span>{page} / {pages}</span>'
                    f'<a class="chip" href="{h(url("/history", f=f, page=page + 1))}"{" style=visibility:hidden" if page >= pages else ""}>古い方 ›</a></div>')
    return layout(ctx, "履歴", "".join(body), "history")


def history_detail_page(ctx, src, store, q) -> str:
    rk = q.get("rk", "")
    r = src.races.get(rk)
    if not r and not store.entries(race_key=rk) and rk not in src.past_keys and not store.note(rk):
        return layout(ctx, "履歴の詳細", empty("レースが見つからないよ", ""), "history", "/history")
    r = r or {"race_key": rk, "venue": rk.split("|")[1], "race_number": rk.split("|")[2], "race_date": rk.split("|")[0]}
    body = [f'<p class="muted" style="margin:0">{h(race_title(r))}・{day_label(r["race_date"])}</p>'
            f'<h1>{h(race_name(r)) or h(race_title(r))}</h1><p class="lead">{race_sub(r) or "&nbsp;"}</p>']
    rows = []
    for tp in TIME_POINTS:
        p = src.pick(rk, "ml", tp)[1]
        rows.append(f'<div class="row" style="padding:4px 0"><span class="chip">{TP_LABEL[tp]}</span>'
                    f'<span>{marks_html(src, rk, p) if p else "<span class=muted>予想なし</span>"}</span></div>')
    res = src.results.get(rk)
    result = ""
    if res and res.get("has_result"):
        pays = res.get("payouts") or {}
        result = (f'<p class="small" style="margin:8px 0 0"><span class="tag f">結果</span>三連複 {h(res.get("label") or "—")}'
                  + ("・払戻 " + "・".join(f"{h(c)} {yen(v)}" if len(pays) > 1 else yen(v) for c, v in pays.items()) if pays
                     else "" if res.get("cancelled") else "・払戻はまだ分からない") + (f"・取消 {'、'.join(map(str, sorted(res['scratched'])))}番" if res.get("scratched") else "")
                  + (f"・{h(res['cancelled'])}・全額返還" if res.get("cancelled") else
                     f"・完走 {res['finishers']}頭（不成立）" if res.get("finishers", 3) < 3 and res.get("complete", True) else "")
                  + (f"・{h(res['incomplete'])}ので精算は保留" if res.get("incomplete") else "") + '</p>')
    body.append(f'<div class="card"><h2>予想の変化（学習）</h2>{"".join(rows)}{result}'
                f'<a class="muted small" style="display:block;margin-top:6px" href="{h(url("/race/timeline", rk=rk))}">変化の理由を見る ›</a></div>')
    note = store.note(rk)
    body.append(f'<div class="card"><div class="row"><h2>自分のメモ</h2><a class="muted small" href="{h(url("/note/edit", rk=rk))}">編集 ›</a></div>'
                f'<div style="white-space:pre-wrap">{h(note) if note else "<span class=muted>まだ無いよ</span>"}</div></div>')
    es = store.entries(race_key=rk, data_mode=src.mode)
    lines = []
    for e in es:
        st = settle_purchase(e, res)
        if e["kind"] == "skip":
            what, money = "見送り", ""
        else:
            what = f'{len(e["stakes"])}点 {yen(e["total"])}'
            money = money_text(st)
        if e.get("mode_mixed"):
            money += "・注意：古い版で見方が変わった履歴あり（最初の記録の見方で集計）"
        lines.append(f'<a href="{h(url("/buy/new", entry=e["entry_id"]))}"><span>{h(what)}<br><span class="muted small">'
                     f'{h(METHOD_LABEL.get(e["method"], e["method"] or ""))} {TP_LABEL.get(e["time_point"], "")} {h(money)}</span></span>'
                     f'<span class="chev">記録を編集 ›</span></a>')
    add = f'<a href="{h(url("/buy/new", rk=rk))}"><span>記録を足す</span><span class="chev">›</span></a>'
    body.append(f'<div class="card"><h2>{"購入記録" if src.mode == "live" else h(real_label(src))}</h2><div class="list">{"".join(lines)}{add}</div></div>')
    return layout(ctx, "履歴の詳細", "".join(body), "history", "/history", "履歴の詳細")


def note_form(ctx, src, store, q) -> str:
    rk = q.get("rk", "")
    body = (f'<h1>メモを書く</h1><p class="lead">{h(rk.replace("|", " "))}</p>'
            f'<form method="post" action="/note"><input type="hidden" name="token" value="{h(ctx["token"])}">'
            f'<input type="hidden" name="rk" value="{h(rk)}"><div class="card"><textarea name="text" placeholder="気づいたこと・判断の理由">'
            f'{h(store.note(rk))}</textarea><p class="muted small">前のメモは消さずに残して、新しい方を表示するよ</p></div>'
            f'<div class="btns"><a class="btn" href="{h(url("/history/detail", rk=rk))}">やめる</a><button class="btn pri" type="submit">保存する</button></div></form>')
    return layout(ctx, "メモ", body, "history", url("/history/detail", rk=rk), "メモ")


# ======================= 結果 =======================

N_CHOICES = (("50", "直近50R"), ("100", "100R"), ("500", "500R"), ("all", "全部"))


def _recent_keys(src, keys, n: str) -> set[str]:
    ks = sorted(set(keys), key=lambda k: race_sort_key(src, k), reverse=True)
    return set(ks if n == "all" else ks[:int(n)])


def one_per_race(src, wallet: str) -> list[dict]:
    """1レース1方式につき1つの精算（時点が複数あれば遅い方。検証データは 24時間前と1時間前の両方がある）。"""
    order = {t: i for i, t in enumerate(TIME_POINTS)}
    vorder = {v["version"]: i for i, v in enumerate(src.versions)}

    def rank(s):   # 遅い時点 → 画面に出している予想と同じ版 → 新しい版
        shown = (src.preds.get(s["race_key"], {}).get((s["method"], s["time_point"])) or {}).get("version")
        return (order.get(s["time_point"], -1), s.get("version") == shown, vorder.get(s.get("version"), -1))
    best: dict = {}
    for s in src.settlements:
        if s["wallet"] != wallet:
            continue
        k = (s["method"], s["race_key"])
        if k not in best or rank(s) > rank(best[k]):
            best[k] = s
    return list(best.values())


def wallet_table(src, wallet: str, keys: set[str] | None = None) -> list[dict]:
    rows = defaultdict(lambda: {"races": 0, "bet": 0, "hit": 0, "stake": 0, "returned": 0, "pending": 0})
    for s in one_per_race(src, wallet):
        if keys is not None and s["race_key"] not in keys:
            continue
        x = rows[s["method"]]
        if s.get("status") == "unsettled_no_payout":   # 払戻が分からない：0円にせず、回収率から外す
            x["pending"] += 1
            continue
        x["races"] += 1
        x["bet"] += int((s.get("stake") or 0) > 0)
        x["hit"] += int(bool(s.get("hits")))
        x["stake"] += s.get("stake") or 0
        x["returned"] += s.get("returned") or 0
    out = []
    for m in [m for m in METHOD_LABEL if m in rows] + sorted(set(rows) - set(METHOD_LABEL)):
        x = rows[m]
        out.append({"method": m, **x, "roi": x["returned"] / x["stake"] if x["stake"] else None})
    return out


def _real(src, store, keys: set[str] | None = None) -> dict:
    """本人の記録の集計。レース数は同じレースの記録をまとめて1R。結果待ち・払戻待ちは金額と回収率に入れない。"""
    tot = {"stake": 0, "returned": 0, "pending": 0, "unsettled": 0, "entries": 0}
    races: set = set()
    for e in store.entries(data_mode=src.mode):
        if e["kind"] != "buy" or (keys is not None and e["race_key"] not in keys):
            continue
        st = settle_purchase(e, src.results.get(e["race_key"]))
        if st["status"] == "pending":
            tot["pending"] += 1
            continue
        if not st["counted"]:
            tot["unsettled"] += 1
            continue
        races.add(e["race_key"])
        tot["entries"] += 1
        tot["stake"] += st["stake"]
        tot["returned"] += st["returned"]
    tot["races"] = len(races)
    tot["roi"] = tot["returned"] / tot["stake"] if tot["stake"] else None
    return tot


def counting_note(src) -> str:
    """仮想の成績の数え方と、数えていない件数。"""
    if src.mode != "live":
        return ""
    late, unknown = src.excluded["after_limit"], src.excluded["post_unknown"]
    return ('<p class="muted small">仮想は、締切＋10分までに最初に保存した版だけを数えるよ'
            + (f"。締切＋10分より後に保存した予想 {late:,}件は数えていない" if late else "")
            + (f"。発走時刻が分からない予想 {unknown:,}件も数えていない" if unknown else "") + "</p>")


def results_page(ctx, src, store, q) -> str:
    n = q.get("n") if q.get("n") in dict(N_CHOICES) else "100"
    wallet = MAIN_WALLET[src.mode]
    settled_keys = {s["race_key"] for s in src.settlements if s["wallet"] == wallet}
    buy_keys = {e["race_key"] for e in store.entries(data_mode=src.mode) if e["kind"] == "buy"}
    keys = _recent_keys(src, settled_keys, n)
    rows = {x["method"]: x for x in wallet_table(src, wallet, keys)}
    ml = rows.get("ml_lgbm")
    real = _real(src, store, _recent_keys(src, buy_keys, n))
    body = [f"<h1>結果</h1><p class='lead'>仮想の財布と{h(real_label(src))}は分けて見るよ</p>",
            seg(N_CHOICES, n, lambda k: url("/results", n=k)),
            f'<div class="kpi"><div><span class="muted small">仮想・学習の回収率</span><b>{pct(ml["roi"]) if ml else "—"}</b>'
            f'<span class="muted small">{ml["races"] if ml else 0}R・{h(WALLET_LABEL[wallet])}</span></div>'
            f'<div><span class="muted small">{h(real_label(src))}の回収率</span><b>{pct(real["roi"])}</b>'
            f'<span class="muted small">{real["races"]}R'
            f'{"・結果待ち" + str(real["pending"]) + "件" if real["pending"] else ""}'
            f'{"・払戻待ち" + str(real["unsettled"]) + "件" if real["unsettled"] else ""}</span></div></div>']
    if ml and ml.get("pending"):
        body.append(f'<p class="muted small">仮想：払戻が分からない {ml["pending"]}R は回収率に入れていないよ</p>')
    if src.mode == "live":
        body.append('<p class="muted small">初週は回収率の判定をしない取り決め。数週たまるまでは参考として見てね</p>')
    else:
        body.append('<p class="muted small">検証データの「仮想」は、オッズを見ずに上位10組を100円ずつ買ったと仮定した参考の数字</p>')
    body.append(counting_note(src))
    body.append(link_list([("方式ごと", "学習・まむ・統合・基準をくらべる", url("/results/methods", n=n)),
                           ("条件別", "距離・馬場・クラスごと", url("/results/conditions", n=n)),
                           ("見送りの振り返り", "見送ったレースはどうだった？", url("/results/skips", n=n)),
                           (f"{real_label(src)}の内訳", "1件ずつ：払戻・返還・結果待ち・払戻待ち", "/purchases")]))
    return layout(ctx, "結果", "".join(body), "results")


def results_methods_page(ctx, src, store, q) -> str:
    n = q.get("n") if q.get("n") in dict(N_CHOICES) else "100"
    wallets = sorted({s["wallet"] for s in src.settlements}, key=lambda w: list(WALLET_LABEL).index(w) if w in WALLET_LABEL else 99)
    wallet = q.get("w") if q.get("w") in wallets else (MAIN_WALLET[src.mode] if MAIN_WALLET[src.mode] in wallets else (wallets[0] if wallets else None))
    body = ["<h1>方式ごと</h1><p class='lead'>同じ物差しで方式をくらべる</p>",
            seg(N_CHOICES, n, lambda k: url("/results/methods", n=k, w=wallet))]
    if not wallet:
        body.append(empty("まだ0件だよ", "精算が出ると、方式ごとの成績が並ぶよ。"))
        return layout(ctx, "方式ごと", "".join(body), "results", url("/results", n=n), "方式ごと")
    if len(wallets) > 1:
        body.append(chips([(w, WALLET_LABEL.get(w, w)) for w in wallets], wallet, lambda w: url("/results/methods", n=n, w=w)))
    keys = _recent_keys(src, {s["race_key"] for s in src.settlements if s["wallet"] == wallet}, n)
    table = wallet_table(src, wallet, keys)
    rows = "".join(f'<tr><td>{h(METHOD_LABEL.get(x["method"], x["method"]))}</td><td>{x["races"]}</td><td>{x["bet"]}</td>'
                   f'<td>{x["hit"]}</td><td>{pct(x["roi"])}</td></tr>' for x in table)
    waiting = sum(x["pending"] for x in table)
    body.append(f'<div class="card"><table><tr><th>方式</th><th>R</th><th>買った</th><th>的中</th><th>回収率</th></tr>{rows}</table></div>'
                f'<p class="muted small">R＝精算したレース、買った・的中＝レース数。{h(WALLET_LABEL.get(wallet, wallet))}。まむは予想が届いたレースだけ。基準と比べて上回るかを見るよ'
                + (f'。払戻が分からない {waiting}件は入れていない' if waiting else "") + '</p>')
    body.append(counting_note(src))
    return layout(ctx, "方式ごと", "".join(body), "results", url("/results", n=n), "方式ごと")


def results_conditions_page(ctx, src, store, q) -> str:
    n = q.get("n") if q.get("n") in dict(N_CHOICES) else "100"
    wallet = MAIN_WALLET[src.mode]
    methods = [m for m in METHOD_LABEL if any(s["method"] == m and s["wallet"] == wallet for s in src.settlements)]
    m = q.get("m") if q.get("m") in methods else ("ml_lgbm" if "ml_lgbm" in methods else (methods[0] if methods else None))
    dim = q.get("d") if q.get("d") in ("dist", "surface", "class", "month") else "dist"
    body = ["<h1>条件別</h1><p class='lead'>どんなレースで強い・弱い？</p>",
            seg([("dist", "距離"), ("surface", "芝・ダ"), ("class", "クラス"), ("month", "月別")], dim, lambda d: url("/results/conditions", n=n, m=m, d=d))]
    if not m:
        body.append(empty("まだ0件だよ", "精算が出ると、条件ごとの成績が並ぶよ。"))
        return layout(ctx, "条件別", "".join(body), "results", url("/results", n=n), "条件別")
    if len(methods) > 1:
        body.append(chips([(x, METHOD_LABEL[x]) for x in methods], m, lambda x: url("/results/conditions", n=n, m=x, d=dim)))
    keys = _recent_keys(src, {s["race_key"] for s in src.settlements if s["wallet"] == wallet and s["method"] == m}, n)
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for s in one_per_race(src, wallet):
        if s["method"] != m or s["race_key"] not in keys or s.get("status") == "unsettled_no_payout":
            continue
        r = src.races.get(s["race_key"], {})
        key = {"dist": distance_band(r.get("distance_m")), "surface": r.get("surface") or "不明",
               "class": r.get("race_class") or "不明", "month": s["race_key"][:7]}[dim]
        a = agg[key]
        a[0] += 1
        a[1] += int(bool(s.get("hits")))
        a[2] += s.get("stake") or 0
        a[3] += s.get("returned") or 0
    rows = "".join(f'<tr><td>{h(k)}</td><td>{v[0]}</td><td>{pct(v[1] / v[0]) if v[0] else "—"}</td><td>{pct(v[3] / v[2]) if v[2] else "—"}</td></tr>'
                   for k, v in (sorted(agg.items(), reverse=True) if dim == "month" else sorted(agg.items(), key=lambda x: -x[1][0])))
    body.append(f'<div class="card"><table><tr><th>{ {"dist": "距離", "surface": "芝・ダート", "class": "クラス", "month": "月"}[dim]}</th>'
                f'<th>R</th><th>的中率</th><th>回収率</th></tr>{rows or "<tr><td colspan=4 class=muted>—</td></tr>"}</table></div>'
                f'<p class="muted small">件数が少ない条件は、たまたまの差が大きいよ（20R未満は参考）</p>')
    body.append(counting_note(src))
    return layout(ctx, "条件別", "".join(body), "results", url("/results", n=n), "条件別")


def results_skips_page(ctx, src, store, q) -> str:
    n = q.get("n") if q.get("n") in dict(N_CHOICES) else "100"
    body = ["<h1>見送りの振り返り</h1><p class='lead'>見送ったレースで、本命の組は来た？</p>"]
    alloc = MAIN_WALLET["live"]
    skipped = []
    if src.mode == "live":
        for rk in src.preds:
            tp = src.latest_tp(rk)
            p = src.pick(rk, "ml", tp)[1] if tp else None
            if p and is_skip(p, alloc):
                skipped.append((rk, p))
    user = [e for e in store.entries(data_mode=src.mode) if e["kind"] == "skip"]
    keys = _recent_keys(src, [rk for rk, _ in skipped] + [e["race_key"] for e in user], n)
    skipped = [(rk, p) for rk, p in skipped if rk in keys]
    def wins(rk):
        res = src.results.get(rk) or {}
        return set(res.get("payouts") or {}) | ({res["actual"]} if res.get("actual") else set())
    came = [rk for rk, p in skipped if p.get("trios") and p["trios"][0]["combo"] in wins(rk)]
    done = [rk for rk, _ in skipped if (src.results.get(rk) or {}).get("has_result")]
    body.append(f'<div class="kpi"><div><span class="muted small">学習が見送った</span><b>{len(skipped)}R</b>'
                f'<span class="muted small">結果が出たのは {len(done)}R</span></div>'
                f'<div><span class="muted small">本命の組が来た</span><b>{len(came)}R</b>'
                f'<span class="muted small">自分の見送り {len([e for e in user if e["race_key"] in keys])}R</span></div></div>')
    if src.mode == "check":
        body.append('<p class="muted small">検証データには発走前のオッズが無く配分していないので、見送りは0件になるよ</p>')
    if came:
        body.append(link_list([(race_title(src.races.get(rk, {"venue": rk.split("|")[1], "race_number": rk.split("|")[2]})),
                                day_label(rk.split("|")[0]), url("/history/detail", rk=rk)) for rk in came[:10]]))
    return layout(ctx, "見送りの振り返り", "".join(body), "results", url("/results", n=n), "見送りの振り返り")


# ======================= 改善 =======================

def improve_page(ctx, src, store, q) -> str:
    body = ["<h1>改善</h1><p class='lead'>今の版と候補を分けて見る</p>"]
    cur = next((v for v in reversed(src.versions) if v["version"] == src.version), None)
    if cur:
        label = ("今の版", "運用中") if src.mode == "live" else ("検証の版", "検証データ")
        body.append(f'<div class="card"><div class="row"><h2>{label[0]}</h2><span class="chip ok">{label[1]}</span></div>'
                    f'<div style="font-weight:700">{h(cur["version"])}</div><p class="muted small" style="margin:2px 0 0">'
                    f'{h(cur["first"][:10])} から・{cur.get("races_official", cur["races"]):,}R の正式な予想</p></div>')
    else:
        body.append(empty("今の版はまだ無いよ", "これからのレースの予想が保存されると表示するよ。"))
    body.append('<div class="card"><div class="row"><h2>くらべている候補</h2><span class="chip warn">並行して記録中</span></div>'
                '<div class="small">・ルールの比較版（v1.1 ほか）：役割は説明・確認。配分なし</div>'
                '<div class="small">・あいす統合 3版（市場のみ・市場＋学習・市場＋学習＋まむ）：別の財布</div>'
                '<p class="muted small" style="margin:6px 0 0">採用・反映は別に決める（最初の数回は、たかむねが新旧をくらべて決める）</p></div>')
    body.append(link_list([("新旧くらべ", "変更の前後の成績と、採用の判断", "/improve/versions"),
                           ("比較の詳細", "検証データでの当たり具合", "/improve/compare"), ("変更履歴", "版ごとの記録", "/improve/history")]))
    return layout(ctx, "改善", "".join(body), "improve")


def improve_compare_page(ctx, src, store, q, metrics: dict | None) -> str:
    body = ["<h1>比較の詳細</h1><p class='lead'>同じレースで、方式ごとの当たり具合</p>"]
    rows = [s for s in (metrics or {}).get("summary", []) if s.get("period") == "holdout" and s.get("time_point") == "1h"]
    if not rows:
        body.append(empty("比較の記録が無いよ", "keiba evaluate の結果（metrics.json）が見つからないよ。"))
    else:
        rows.sort(key=lambda s: list(METHOD_LABEL).index(s["method"]) if s["method"] in METHOD_LABEL else 99)
        t = "".join(f'<tr><td>{h(METHOD_LABEL.get(s["method"], s["method"]))}</td><td>{s["races"]:,}</td>'
                    f'<td>{pct(s.get("hit@10"), 1)}</td><td>{pct((s.get("ref_top10_equal") or {}).get("roi"))}</td></tr>' for s in rows)
        per = (metrics or {}).get("period") or ["", ""]
        body.append(f'<div class="card"><h2>封印期間・1時間前</h2><table><tr><th>方式</th><th>R</th><th>上位10組に正解</th>'
                    f'<th>参考回収率</th></tr>{t}</table></div>'
                    f'<p class="muted small">版 {h((metrics or {}).get("version"))}。参考回収率はオッズを見ずに上位10組を100円ずつ買った仮定。'
                    f'人気順は確定オッズを使うので比較専用。これからのレースの成績は「結果」で見てね</p>')
    return layout(ctx, "比較の詳細", "".join(body), "improve", "/improve", "比較の詳細")


DECISIONS = {"adopt": "採用したい", "hold": "保留（もう少し見る）", "reject": "見送る（今の版のまま）"}


def decision_key(va: str, vb: str) -> str:
    return f"improve|{va}|{vb}"


def improve_versions_page(ctx, src, store, q, compare) -> str:
    """新旧くらべ：2つの版を、両方にあるレースだけで比べる。本人の判断を記録する（運用の版は変えない）。"""
    vs = [v["version"] for v in src.versions if not v["version"].startswith("live-")]
    body = ["<h1>新旧くらべ</h1><p class='lead'>変更の前と後で、成績はどう変わった？</p>"]
    if len(vs) < 2:
        body.append(empty("くらべる版が足りないよ", "検証の版が2つ以上たまると、ここでくらべられるよ。"))
        return layout(ctx, "新旧くらべ", "".join(body), "improve", "/improve", "新旧くらべ")
    vb = q.get("b") if q.get("b") in vs else vs[-1]
    va = q.get("a") if q.get("a") in vs and q.get("a") != vb else next(v for v in reversed(vs) if v != vb)
    first = {v["version"]: v["first"][:16].replace("T", " ") for v in src.versions}

    def select(name, cur):
        return (f'<select name="{name}" aria-label="{"前の版" if name == "a" else "後の版"}">' + "".join(
            f'<option value="{h(v)}"{" selected" if v == cur else ""}>{h(v)}（{h(first.get(v, ""))}）</option>'
            for v in reversed(vs)) + "</select>")
    body.append(f'<form method="get" action="/improve/versions" class="card"><label>前の版</label>{select("a", va)}'
                f'<label style="margin-top:8px">後の版</label>{select("b", vb)}'
                f'<div class="btns" style="margin:10px 0 0"><button class="btn" type="submit">くらべる</button></div></form>')
    res = compare(va, vb)
    if res.get("stale"):
        body.append(f'<div class="mode">{h(res["stale"])}（次の表示で計算し直すよ）</div>')
    if not res["rows"]:
        body.append(empty("両方にあるレースが無いよ", "ほかの版を選んでね。"))
    else:
        def cell(x, key):
            return pct(x[key]) if key == "roi" else f'{x[key]:,}'
        rows = []
        for x in res["rows"]:
            d = None if x["a"]["roi"] is None or x["b"]["roi"] is None else x["b"]["roi"] - x["a"]["roi"]
            hit_d = x["b"]["hit"] - x["a"]["hit"]
            rows.append(f'<tr><td>{h(METHOD_LABEL.get(x["method"], x["method"]))}</td><td>{x["races"]:,}</td>'
                        f'<td>{cell(x["a"], "hit")}→{cell(x["b"], "hit")}<br><span class="muted small">{hit_d:+d}</span></td>'
                        f'<td>{pct(x["a"]["roi"])}→{pct(x["b"]["roi"])}<br><span class="muted small">'
                        f'{"—" if d is None else "±0.0pt" if abs(d) < 0.0005 else f"{d * 100:+.1f}pt"}</span></td></tr>')
        body.append(f'<div class="card"><h2>前 → 後（{TP_LABEL.get(res["tp"], res["tp"])}）</h2><table><tr><th>方式</th><th>R</th>'
                    f'<th>的中R</th><th>参考回収率</th></tr>{"".join(rows)}</table>'
                    f'<p class="muted small" style="margin:6px 0 0">両方の版にある {res["races"]:,}R だけで比べた。'
                    f'{h(WALLET_LABEL.get(res["wallet"], res["wallet"]))}の仮定。たまたまの差もあるので、差が小さいときは決めつけないでね</p>'
                    + ('<p class="small" style="margin:6px 0 0"><span class="tag f">事実</span>この2つの版では、どの方式も的中と回収の数字が同じ'
                       '<br><span class="tag g">推論</span>予想が同じだった可能性はあるけど、この画面は精算の数字だけを見ているので、予想そのものが同じかは分からない</p>'
                       if all(x["a"] == x["b"] for x in res["rows"]) else "") + '</div>')
    key = decision_key(va, vb)
    prev = store.note(key)
    opts = "".join(f'<label><input type="radio" name="decision" value="{k}"{" checked" if prev.startswith(v) else ""}> {h(v)}</label>'
                   for k, v in DECISIONS.items())
    body.append(f'<form method="post" action="/improve/decision"><input type="hidden" name="token" value="{h(ctx["token"])}">'
                f'<input type="hidden" name="a" value="{h(va)}"><input type="hidden" name="b" value="{h(vb)}">'
                f'<div class="card"><h2>たかむねの判断</h2>'
                + (f'<p class="small" style="margin:0 0 8px;white-space:pre-wrap"><span class="tag f">記録</span>{h(prev)}</p>' if prev else "")
                + f'<div class="opt">{opts}</div><textarea name="memo" style="margin-top:8px" placeholder="決めた理由"></textarea>'
                f'<p class="muted small" style="margin:6px 0 0">ここは判断の記録だけ。運用の版の切り替えは別の作業で行うよ</p></div>'
                f'<div class="btns"><button class="btn pri" type="submit">判断を記録する</button></div></form>')
    return layout(ctx, "新旧くらべ", "".join(body), "improve", "/improve", "新旧くらべ")


def improve_history_page(ctx, src, store, q) -> str:
    vs = list(reversed(src.versions))[:20]
    body = ["<h1>変更履歴</h1><p class='lead'>台帳に残っている版</p>"]
    if not vs:
        body.append(empty("まだ0件だよ", ""))
    else:
        now_tag = ' <span class="chip ok">今の版</span>'
        rows = "".join(f'<div style="display:block"><b>{h(v["version"])}</b>{now_tag if v["version"] == src.version else ""}'
                       f'<div class="muted small">{h(v["first"][:10])}〜{h(v["last"][:10])}・{v["races"]:,}R・{v["predictions"]:,}件'
                       + (f'（正式は {v["races_official"]:,}R）' if v.get("races_official", v["races"]) != v["races"] else "")
                       + '</div></div>' for v in vs)
        body.append(f'<div class="card list tl" style="border-left:0;padding-left:14px">{rows}</div>')
    return layout(ctx, "変更履歴", "".join(body), "improve", "/improve", "変更履歴")


# ======================= メニュー =======================

def menu_page(ctx, src, store, q) -> str:
    body = ("<h1>メニュー</h1><p class='lead'>たまに使うもの</p>" +
            link_list([("データの状態", "取得時刻・取り損ね・オッズの確認", "/status"),
                       ("購入記録の一覧", "全部の記録と書き出し", "/purchases"),
                       ("表示の設定", f"いま：{MODES[src.mode]}", "/settings"),
                       ("このアプリについて", "注意書き", "/about")]))
    return layout(ctx, "メニュー", body, None, "/races", "メニュー")


def status_page(ctx, src, store, q, extra: dict) -> str:
    health, odds = extra.get("health") or {}, extra.get("odds") or {}
    lines = [("収集の最終確認", (health.get("checked_at") or "—")[:16].replace("T", " ")),
             ("知っているレース", f'{health.get("races_known", len(src.races)):,}R' if health or src.races else "—"),
             ("取り損ね", f'{len(health.get("missed") or [])}件'), ("失敗", f'{len(health.get("errors") or [])}件')]
    body = ["<h1>データの状態</h1><p class='lead'>古い情報を最新と見せないための確認</p>",
            '<div class="card"><h2>収集</h2>' + "".join(f'<div class="row small" style="padding:3px 0"><span class="muted">{h(a)}</span><span>{h(b)}</span></div>' for a, b in lines) + "</div>"]
    if odds:
        body.append(f'<div class="card"><h2>オッズの確認</h2><div class="row small"><span class="muted">合格</span><span>{odds["pass"]} / {odds["total"]}</span></div>'
                    f'<p class="muted small" style="margin:4px 0 0">keiba live check の結果（{h(odds["file_time"])}）</p></div>')
    else:
        body.append('<div class="card"><h2>オッズの確認</h2><p class="muted small" style="margin:0">まだ判定の記録が無いよ（初回は開催日）</p></div>')
    body.append(f'<div class="card"><h2>このアプリが読んだもの</h2><div class="small">{h(MODES[src.mode])}・予想 {sum(len(v) for v in src.preds.values()):,}件・'
                f'精算 {len(src.settlements):,}件</div><div class="muted small">読み込み {datetime.fromtimestamp(src.loaded_at):%m/%d %H:%M}</div>'
                + "".join(f'<div class="err small">{h(n)}</div>' for n in src.notes) + "</div>")
    for w in (health.get("errors") or [])[:3]:
        body.append(f'<div class="err small">{h(w)}</div>')
    return layout(ctx, "データの状態", "".join(body), None, "/menu", "データの状態")


def purchases_page(ctx, src, store, q) -> str:
    es = store.entries(data_mode=src.mode, include_void=False)
    lead = "実際に買った分の記録（新しい順）" if src.mode == "live" else "検証データで付けた練習の記録（実際のお金ではない）"
    body = [f"<h1>購入記録の一覧</h1><p class='lead'>{lead}</p>"]
    if not es:
        body.append(empty("まだ0件だよ", "レースの詳細から「購入を記録」で足せるよ。"))
    else:
        rows = []
        for e in es[:100]:
            st = settle_purchase(e, src.results.get(e["race_key"]))
            cell = {"settled": lambda: yen(st["returned"]) + (f'<br><span class="muted small">うち返還 {yen(st["refund"])}</span>' if st["refund"] else ""),
                    "refunded_void": lambda: yen(st["returned"]) + '<br><span class="muted small">返還（'
                    + ("取りやめ" if st.get("cancelled") else "不成立") + '）</span>',
                    "pending": lambda: '<span class="muted">結果待ち</span>',
                    "partial_result": lambda: '<span class="muted">保留（結果の一部だけ）</span>',
                    "unsettled_no_payout": lambda: '<span class="muted">払戻待ち</span>'}.get(st["status"], lambda: "—")()
            if st["status"] in ("partial_result", "unsettled_no_payout") and st.get("refund"):
                cell += f'<br><span class="muted small">取消の返還 {yen(st["refund"])} は確定</span>'

            rows.append(f'<tr><td><a href="{h(url("/history/detail", rk=e["race_key"]))}">{h(e["race_key"].replace("|", " "))}R</a></td>'
                        f'<td>{"見送り" if e["kind"] == "skip" else yen(e["total"])}</td><td>{cell}</td></tr>')
        body.append(f'<div class="card"><table><tr><th>レース</th><th>購入</th><th>払戻</th></tr>{"".join(rows)}</table></div>'
                    '<p class="muted small">結果待ち・保留・払戻待ちは、結果の画面の金額と回収率に入れていないよ</p>')
    body.append(f'<p><a class="chip ok" href="/purchases.csv">CSVで書き出す</a> <span class="muted small">Macに保存されるよ</span></p>')
    return layout(ctx, "購入記録の一覧", "".join(body), None, "/menu", "購入記録の一覧")


def settings_page(ctx, src, store, q) -> str:
    def opt(name, value, label, cur):
        return f'<label><input type="radio" name="{name}" value="{value}"{" checked" if cur == value else ""}> {h(label)}</label>'
    body = (f'<h1>表示の設定</h1><p class="lead">このブラウザだけに保存するよ</p>'
            f'<form method="post" action="/settings"><input type="hidden" name="token" value="{h(ctx["token"])}">'
            f'<div class="card"><h2>見るデータ</h2><div class="opt">{opt("mode", "live", MODES["live"], src.mode)}{opt("mode", "check", MODES["check"], src.mode)}</div>'
            f'<p class="muted small" style="margin:8px 0 0">検証データは画面の確認用。発走前の予想ではないよ</p></div>'
            f'<div class="card"><h2>画面の色</h2><div class="opt">{opt("theme", "auto", "自動", ctx.get("theme", "auto"))}'
            f'{opt("theme", "light", "明るい", ctx.get("theme"))}{opt("theme", "dark", "暗い", ctx.get("theme"))}</div></div>'
            f'<div class="card"><h2>文字の大きさ</h2><div class="opt">{opt("size", "normal", "ふつう", ctx.get("size", "normal"))}'
            f'{opt("size", "large", "大きめ", ctx.get("size"))}</div></div>'
            f'<div class="card"><h2>自動で読み直す</h2><div class="opt">{opt("refresh", "on", "1分ごと", ctx.get("refresh", "on"))}'
            f'{opt("refresh", "off", "しない", ctx.get("refresh"))}</div><p class="muted small" style="margin:8px 0 0">'
            f'これからのレースの一覧と詳細だけ。入力の画面では読み直さないよ</p></div>'
            f'<div class="btns"><button class="btn pri" type="submit">保存する</button></div></form>')
    return layout(ctx, "表示の設定", body, None, "/menu", "表示の設定")


def about_page(ctx, src, store, q) -> str:
    items = ["予想を見る・購入を記録するためのアプリ。自動で買うことはしないよ",
             "的中や回収率110%を保証するものではないよ",
             "「推論」は方式の推定。事実とは分けて表示しているよ",
             "仮想の財布（模擬）と、実際に買った記録は別々に集計するよ",
             "予想の台帳は読むだけ。購入記録とメモは別のファイルに追記して、消さずに残すよ",
             where_text(ctx)]
    body = "<h1>このアプリについて</h1><p class='lead'>三連複の確認アプリ</p><div class='card'>" + \
           "".join(f"<div class='small' style='padding:4px 0'>・{h(x)}</div>" for x in items) + "</div>"
    return layout(ctx, "このアプリについて", body, None, "/menu", "このアプリについて")


def purchases_csv(src, store) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["entry_id", "rev", "race_key", "kind", "data_mode", "method", "time_point", "stakes", "total", "memo",
                "first_saved", "updated", "actual", "settle_status", "returned"])
    for e in store.entries(data_mode=src.mode, include_void=True):
        res = src.results.get(e["race_key"]) or {}
        st = settle_purchase(e, res)
        w.writerow([csv_cell(x) for x in (
            e["entry_id"], e["rev"], e["race_key"], e["kind"], e["data_mode"], e["method"] or "", e["time_point"] or "",
            json.dumps(e["stakes"], ensure_ascii=False), e["total"], e["memo"], e["created_at"], e["updated_at"],
            res.get("label") or "", st["status"], st["returned"] if st.get("counted") else "")])
    return buf.getvalue()


def csv_cell(v):
    """表計算ソフトが式として動かさないように、= + - @ などで始まる文字は先頭に ' を付けて文字として扱わせる。"""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r", "|", "%"):
        return "'" + v
    return v


def login_page(ctx, error: str = "") -> str:
    body = (f'<h1>合言葉</h1><p class="lead">同じWi-Fiから開くときだけ聞くよ</p>'
            + (f'<div class="err">{h(error)}</div>' if error else "")
            + '<form method="post" action="/login"><div class="card"><label for="pw">合言葉</label>'
            '<input id="pw" name="passphrase" type="password" autocomplete="current-password" autofocus></div>'
            '<div class="btns"><button class="btn pri" type="submit">開く</button></div></form>'
            '<p class="muted small">合言葉は、アプリを動かしているMacの設定（.env）に書いてあるよ</p>')
    return layout({**ctx, "mode": "live"}, "合言葉", body, None, None, "三連複の確認")
