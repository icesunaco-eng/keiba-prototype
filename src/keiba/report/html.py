"""iPhone幅で読める静的HTMLレポート（成績・レース一覧・レース詳細）。台帳は読取専用で開く。"""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from ..ledger import Ledger
from ..models.rule import GROUP_LABELS, GROUPS

METHOD_LABEL = {"ml_lgbm": "学習モデル", "rule": "ルール（あいす v1.0）", "rule_v01": "ルール（暫定 v0.1）", "base_logit": "比較：ロジスティック",
                "base_pop": "比較：人気順（確定オッズ）"}
TP_LABEL = {"24h": "前日", "1h": "1時間前", "15m": "15分前"}
PERIOD_LABEL = {"walk_forward": "時間順の検証", "holdout": "封印期間（1回だけ評価）"}

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e1db;--card:#fff;--accent:#1f6f5c;--hit:#e8f4ef;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ececec;--muted:#a0a0a5;--line:#2c2c2e;--card:#1c1c1e;--accent:#5cc3a5;--hit:#16352c;--warn:#e0b050}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:1.25rem;margin:.2rem 0 .6rem}h2{font-size:1.05rem;margin:1.4rem 0 .5rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0}
.muted{color:var(--muted);font-size:.85rem}.warn{color:var(--warn);font-size:.85rem}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}
th{font-weight:600;font-size:.8rem;color:var(--muted)}tr.hit td{background:var(--hit)}a{color:var(--accent);text-decoration:none}
.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 8px;font-size:.8rem;margin-right:4px}
nav a{margin-right:12px}
"""


def _page(title: str, body: str, depth: int = 0) -> str:
    up = "../" * depth
    return (f"<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><main><nav class='muted'><a href='{up}index.html'>成績</a>"
            f"<a href='{up}races.html'>レース一覧</a></nav>{body}"
            f"<p class='muted'>検証用の試作。実際の購入はしていない。数字は暫定。</p></main></body></html>")


def _fmt(v, pct=False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    return f"{v*100:.1f}%" if pct else (f"{v:,}" if isinstance(v, int) else f"{v:.3f}")


def _slug(race_key: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", race_key.encode("unicode_escape").decode()) + ".html"


def build_report(ledger_path: Path, out_dir: Path, build_report_path: Path | None = None, version: str | None = None) -> Path:
    led = Ledger(ledger_path, read_only=True)
    runs = led.conn.execute("SELECT run_id, payload FROM runs ORDER BY created_at DESC").fetchall()
    if not runs:
        raise RuntimeError("台帳に評価の記録がありません。先に keiba evaluate を実行してください")
    run_id, payload = runs[0] if version is None else next(r for r in runs if r[0] == version)
    result = json.loads(payload)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "race").mkdir(exist_ok=True)
    build = json.loads(build_report_path.read_text()) if build_report_path and build_report_path.exists() else {}

    # ---- 成績 ----
    body = [f"<h1>三連複予想の検証</h1><p class='muted'>版 {html.escape(run_id)}／対象 {result['period'][0]}〜{result['period'][1]}・"
            f"{result['races_eligible']:,}レース</p>"]
    if build:
        body.append("<div class='card'><b>データ</b><br>"
                    f"全{build['races']:,}レース中、出走表の項目がそろうのは {build['races_with_attributes']:,}レース。"
                    f"三連複の払戻があるのは {build['races_with_trio_payout']:,}レース。"
                    f"<br><span class='muted'>項目が無いレース（あいす分）：{build.get('races_without_attributes_by_owner', {})}</span></div>")
    for period in ("walk_forward", "holdout"):
        for tp in ("1h", "24h"):
            rows = [s for s in result["summary"] if s["period"] == period and s["time_point"] == tp]
            if not rows:
                continue
            rows.sort(key=lambda s: list(METHOD_LABEL).index(s["method"]))
            t = ["<div class='scroll'><table><tr><th>方式</th><th>レース</th><th>対数損失</th><th>上位5点</th>"
                 "<th>上位10点</th><th>上位20点</th><th>参考回収率（10点）</th><th>95%幅</th></tr>"]
            for s in rows:
                ref = s["ref_top10_equal"]
                ci = ref["roi_95ci"]
                t.append(f"<tr><td>{METHOD_LABEL[s['method']]}</td><td>{s['races']:,}</td><td>{_fmt(s['trio_logloss'])}</td>"
                         f"<td>{_fmt(s['hit@5'], True)}</td><td>{_fmt(s['hit@10'], True)}</td><td>{_fmt(s['hit@20'], True)}</td>"
                         f"<td>{_fmt(ref['roi'], True)}</td><td>{_fmt(ci[0], True)}〜{_fmt(ci[1], True)}</td></tr>")
            t.append("</table></div>")
            uni = rows[0]["uniform_logloss"]
            body.append(f"<h2>{PERIOD_LABEL[period]}・{TP_LABEL[tp]}</h2>{''.join(t)}"
                        f"<p class='muted'>対数損失は小さいほど良い（全組を同じ確率とした場合 {_fmt(uni)}）。"
                        f"上位k点＝確率の高い順にk組を選んだとき、実際の三連複が入っていた割合。"
                        f"参考回収率＝オッズを見ずに上位10組を100円ずつ買ったと仮定（払戻が分かる"
                        f"{rows[0]['ref_top10_equal']['races_settled']:,}レース）。</p>")
    ml = next((s for s in result["summary"] if s["period"] == "holdout" and s["method"] == "ml_lgbm" and s["time_point"] == "1h"), None)
    if ml and ml.get("calibration_top3"):
        t = ["<div class='scroll'><table><tr><th>予想した3着内率</th><th>頭数</th><th>予想の平均</th><th>実際</th></tr>"]
        for b in ml["calibration_top3"]:
            t.append(f"<tr><td>{b['bin']}</td><td>{b['n']:,}</td><td>{_fmt(b['pred'], True)}</td><td>{_fmt(b['actual'], True)}</td></tr>")
        body.append("<h2>確率の当てはまり（学習モデル・封印期間・1時間前）</h2>" + "".join(t) + "</table></div>")
    imp = result.get("importance", {})
    key = next((k for k in imp if k.startswith("holdout:1h")), None)
    if key:
        body.append("<h2>学習モデルがよく使った特徴</h2><div class='card'>" +
                    " ".join(f"<span class='pill'>{html.escape(k)}</span>" for k in list(imp[key])[:12]) + "</div>")
    body.append("<p class='warn'>「比較：人気順」は発走後の確定オッズを使うので、予想には使えない（市場との比較用）。"
                "三連複のオッズが無いため、配分（均等払戻・期待値・ケリー）はすべて「未配分」。</p>")
    (out_dir / "index.html").write_text(_page("三連複予想の検証", "".join(body)), encoding="utf-8")

    # ---- レース一覧と詳細（封印期間） ----
    preds = defaultdict(dict)
    for p in led.predictions(run_id):
        if p.get("period") == "holdout":
            preds[p["race_key"]][(p["method"], p["time_point"])] = p
    sett = {(s["race_key"], s["method"], s["time_point"], s["wallet"]): s for s in led.settlements(run_id)}
    items = []
    for rk in sorted(preds, key=lambda k: (k.split("|")[0], k), reverse=True):
        main_p = preds[rk].get(("ml_lgbm", "1h")) or next(iter(preds[rk].values()))
        s10 = sett.get((rk, main_p["method"], main_p["time_point"], "ref_top10_equal"), {})
        hit = bool(s10.get("hits"))
        top = main_p["trios"][0]["combo"] if main_p["trios"] else "–"
        items.append(f"<tr class='{'hit' if hit else ''}'><td><a href='race/{_slug(rk)}'>{html.escape(rk)}</a></td>"
                     f"<td>{main_p['confidence']}</td><td>{top}</td><td>{'的中' if hit else ''}</td></tr>")
        _race_page(out_dir / "race" / _slug(rk), rk, preds[rk], sett)
    body = ("<h1>レース一覧（封印期間）</h1><p class='muted'>学習モデル・1時間前の予想。的中＝上位10組に実際の三連複が入った。</p>"
            "<div class='scroll'><table><tr><th>レース</th><th>信頼度</th><th>本命の組</th><th></th></tr>" + "".join(items) + "</table></div>")
    (out_dir / "races.html").write_text(_page("レース一覧", body), encoding="utf-8")
    led.close()
    return out_dir / "index.html"


def _race_page(path: Path, rk: str, preds: dict, sett: dict):
    parts = [f"<h1>{html.escape(rk)}</h1>"]
    for (method, tp) in sorted(preds, key=lambda x: (x[1] != "1h", list(METHOD_LABEL).index(x[0]))):
        p = preds[(method, tp)]
        s10 = sett.get((rk, method, tp, "ref_top10_equal"), {})
        hits = set(s10.get("hits", []))
        rows = "".join(f"<tr class='{'hit' if t['combo'] in hits else ''}'><td>{t['combo']}</td><td>{t['p']*100:.2f}%</td></tr>"
                       for t in p["trios"][:10])
        parts.append(f"<h2>{METHOD_LABEL[method]}・{TP_LABEL[tp]} <span class='pill'>信頼度 {p['confidence']}</span></h2>"
                     f"<div class='scroll'><table><tr><th>三連複</th><th>確率</th></tr>{rows}</table></div>"
                     f"<p class='muted'>参考（10点買い）：{s10.get('status', '–')}／払戻 {s10.get('returned', '–')}円"
                     f"{'／' + '、'.join(map(html.escape, p['notes'])) if p.get('notes') else ''}</p>")
        if method == "rule" and tp == "1h":
            head = "".join(f"<th>{GROUP_LABELS[g]}</th>" for g in GROUPS)
            body = "".join("<tr><td>" + str(h["n"]) + "</td>" +
                           "".join(f"<td>{'未' if h['groups'][g] is None else round(h['groups'][g])}</td>" for g in GROUPS) +
                           f"<td>{h['coverage']*100:.0f}%</td></tr>" for h in sorted(p["horses"], key=lambda h: h["n"]))
            parts.append("<h2>ルール方式の採点（10段階・未＝未採点）</h2>"
                         f"<div class='scroll'><table><tr><th>馬番</th>{head}<th>採点割合</th></tr>{body}</table></div>")
    path.write_text(_page(rk, "".join(parts), depth=1), encoding="utf-8")
