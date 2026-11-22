"""月次の振り返り（設計の「月次レビュー」）。これからのレース（台帳の版 live-*）を、月ごとにまとめる。

まとめること：
  1. 仮想の財布：方式×配分ごとの賭け金・払戻・回収率・的中レース（実購入はしない）
  2. 予想の当たり具合：方式×時点ごとに、実際の三連複が上位5・10・20組に入った率
  3. 役割A：食い違いの印（食い違い／食い違いなし／判定できず）ごとの、学習モデルの当たり具合
  4. ルールの比較版：版ごとの当たり具合と、総合点が付く馬の割合（本採用の判断材料。あいすが決める）
  5. オッズの合格判定：時点ごとの合格率と、不合格の理由
  6. 収集：取り損ね・失敗

回収率の達成判定は、取り決め（初週はしない・数週たまるまで参考）に従い、ここでは「判定しない」と明記する。
"""
from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from . import official
from .ledger import Ledger
from .live.collector import ARCHIVE_FILE, read_jsonl
from .live.wallet import _snapshots, live_tables, odds_check
from .report.html import _page

HIT_K = (5, 10, 20)
MARKS = ("食い違い", "食い違いなし", "判定できず")


def _results(paths) -> dict:
    root = paths.data_dir / "live"
    state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"races": {}}
    races = {}
    for x in read_jsonl(root / ARCHIVE_FILE)[0]:   # 予定表から外した（7日より前の）レースの結果も数える
        if all(x.get(k) is not None for k in ("race_key", "race_date", "venue", "race_number")):
            races[x["race_key"]] = {k: v for k, v in x.items() if k != "race_key"}
    races.update(state.get("races", {}))
    _, _, inputs = live_tables(root, {**state, "races": races}, _snapshots(root))
    out = {}
    for (rk, tp), res in inputs.items():
        if tp != "result":
            continue
        fin = sorted((r for r in res["rows"] if r["status"] == "finished" and r["finish_position"]), key=lambda r: r["finish_position"])
        top = [r["horse_number"] for r in fin if r["finish_position"] <= 3]
        out[rk] = {"actual": "-".join(map(str, sorted(top))) if len(top) == 3 else None,
                   "payouts": res["trio_payouts"]}
    return out, state


CMP_METHODS = ["int_market", "ml_lgbm", "int_market_ml", "int_market_ml_mam"]
CMP_LABELS = {"int_market": "市場推定確率のみ（基準）", "ml_lgbm": "学習のみ", "int_market_ml": "市場＋学習 1.0/0.2",
              "int_market_ml_mam": "市場＋学習＋まむ 1.0/0.2/0.2"}


def _in_range(day: str, month: str | None, start: str | None, end: str | None) -> bool:
    if start or end:
        return (not start or day >= start) and (not end or day <= end)
    return day.startswith(month)


def comparison(preds: list, setts: list, results: dict) -> list:
    """同じレース・同じ時点で、市場のみ・学習のみ・統合を比べる（あいすとの取り決め）。
    主の物差しは対数損失（実際の組の確率。全組の確率が正式なものだけ）、補助に上位10組の的中率と仮想回収率。"""
    full = {}
    for p in preds:
        res = results.get(p["race_key"])
        if p["method"] in CMP_METHODS and p.get("trio_probs_full") and res and res["actual"]:
            full[(p["race_key"], p["time_point"], p["method"])] = p
    roi = {}
    for s in setts:
        roi.setdefault((s["race_key"], s["method"]), []).append(s)
    out = []
    for group in (["int_market", "ml_lgbm", "int_market_ml"], ["int_market", "ml_lgbm", "int_market_ml", "int_market_ml_mam"]):
        keys = {(rk, tp) for rk, tp, m in full if m == group[0]}
        for m in group[1:]:
            keys &= {(rk, tp) for rk, tp, mm in full if mm == m}
        if not keys:
            continue
        for m in group:
            ll, hit = [], []
            for rk, tp in keys:
                p = full[(rk, tp, m)]
                actual = results[rk]["actual"]
                ll.append(-np.log(max(p["trio_probs_full"].get(actual, 0.0), 1e-6)))
                hit.append(actual in [t["combo"] for t in p["trios"][:10]])
            st = [x for rk in {rk for rk, _ in keys} for x in roi.get((rk, m), []) if x["wallet"] == "ev_weighted"]
            stake = sum(x["stake"] for x in st)
            out.append({"set": "まむ入り" if "int_market_ml_mam" in group else "基本", "method": CMP_LABELS[m], "races": len(keys),
                        "logloss": round(float(np.mean(ll)), 4), "hit10": round(float(np.mean(hit)), 4),
                        "roi_ev": round(sum(x["returned"] for x in st) / stake, 4) if stake else None, "bet_races": sum(x["stake"] > 0 for x in st)})
    return out


def build_review(paths, month: str | None, start: str | None = None, end: str | None = None) -> dict:
    led = Ledger(paths.ledger_path, read_only=True) if paths.ledger_path.exists() else None
    preds = [p for p in (led.predictions() if led else []) if p["version"].startswith("live-") and _in_range(p["race_date"], month, start, end)]
    setts = [s for s in (led.settlements() if led else []) if s["version"].startswith("live-") and _in_range(s["race_key"][:10], month, start, end)]
    late = unknown = early = 0
    if led:
        sel = official.load(led.conn, paths.data_dir / "live")
        key = lambda x: (x["race_key"], x["time_point"], x["method"], x["version"])
        late, unknown = sum(key(p) in sel.late for p in preds), sum(key(p) in sel.unknown for p in preds)
        early = sum(key(p) in sel.early for p in preds)
        preds = [p for p in preds if sel.is_shown(*key(p))]
        setts = [s for s in setts if sel.is_counted(*key(s))]
        led.close()
    results, state = _results(paths)
    review = {"month": month or f"{start}〜{end}", "races_predicted": len({p["race_key"] for p in preds}),
              "races_with_result": len({p["race_key"] for p in preds if p["race_key"] in results}),
              "versions": sorted({p["version"] for p in preds}),
              "counting": "その時点の材料を取った後、締切＋10分までに最初に保存した版だけを数える（2026-11-22 決定）",
              "excluded": {"before_material": early, "after_limit": late, "post_unknown": unknown},
              "judgement": "回収率の達成判定はしない（取り決め：初週は判定しない。数週たまるまで参考）。実購入はしない"}

    review["comparison"] = comparison(preds, setts, results)

    # 1. 財布
    w = pd.DataFrame(setts)
    if len(w):
        g = w.groupby(["method", "wallet"]).agg(races=("race_key", "nunique"), bet_races=("stake", lambda s: int((s > 0).sum())),
                                                stake=("stake", "sum"), returned=("returned", "sum"),
                                                hit_races=("hits", lambda h: int(sum(bool(x) for x in h)))).reset_index()
        g["roi"] = (g["returned"] / g["stake"].where(g["stake"] > 0)).round(4)
        review["wallets"] = g.to_dict("records")
    else:
        review["wallets"] = []

    # 2〜4. 当たり具合
    rows = []
    for p in preds:
        res = results.get(p["race_key"])
        if not res or not res["actual"]:
            continue
        ranked = [t["combo"] for t in p["trios"]]
        rank = ranked.index(res["actual"]) + 1 if res["actual"] in ranked else None
        cov = [h.get("coverage") for h in p["horses"] if h.get("coverage") is not None]
        rows.append({"race_key": p["race_key"], "method": p["method"], "time_point": p["time_point"], "rank": rank,
                     "mark": (p.get("rule_view") or {}).get("disagreement"),
                     "scored_share": (sum(c >= 0.5 for c in cov) / len(cov)) if cov else None})
    df = pd.DataFrame(rows)

    def hits(d):
        return {f"上位{k}": round(float(((d["rank"].notna()) & (d["rank"] <= k)).mean()), 4) for k in HIT_K}

    review["accuracy"] = [{"method": m, "time_point": tp, "races": len(d), **hits(d)}
                          for (m, tp), d in df.groupby(["method", "time_point"])] if len(df) else []
    ml = df[(df["method"] == "ml_lgbm")] if len(df) else df
    review["role_a"] = [{"mark": mk, "races": int((ml["mark"] == mk).sum()), **(hits(ml[ml["mark"] == mk]) if (ml["mark"] == mk).any() else {})}
                        for mk in MARKS] if len(ml) else []
    rv = df[df["method"].str.startswith("rule")] if len(df) else df
    review["rule_variants"] = [{"method": m, "races": len(d), "総合点が付く馬": round(float(d["scored_share"].mean()), 3)
                                if d["scored_share"].notna().any() else None, **hits(d)}
                               for m, d in rv.groupby("method")] if len(rv) else []

    # 5. オッズの合格判定
    oc = odds_check(paths)
    oc = oc[oc["race_key"].map(lambda k: _in_range(k[:10], month, start, end))] if len(oc) else oc
    review["odds_gate"] = [{"time_point": tp, "checked": len(d), "passed": int(d["pass"].sum()),
                            "reasons": dict(Counter(r for x in d["reasons"] if x for r in x.split(" / ")).most_common(5))}
                           for tp, d in oc.groupby("time_point")] if len(oc) else []

    # 6. 収集
    missed = [k for k in state.get("missed_notified", []) if _in_range(k[:10], month, start, end)]
    review["collection"] = {"missed": len(missed), "missed_examples": missed[:10]}
    return review


def write_review(paths, month: str | None, start: str | None = None, end: str | None = None) -> Path:
    r = build_review(paths, month, start, end)
    month = month or f"{start}_{end}"
    out = paths.data_dir / "review"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{month}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    def table(rows, cols, labels):
        if not rows:
            return "<p class='muted'>まだ無い</p>"
        head = "".join(f"<th>{html.escape(l)}</th>" for l in labels)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(_cell(x.get(c)))}</td>" for c in cols) + "</tr>" for x in rows)
        return f"<div class='scroll'><table><tr>{head}</tr>{body}</table></div>"

    parts = [f"<h1>月次の振り返り {html.escape(month)}</h1>",
             f"<p class='muted'>予想したレース {r['races_predicted']}・結果が分かったレース {r['races_with_result']}・版 {html.escape(', '.join(r['versions']) or '–')}</p>",
             f"<p class='muted'>{html.escape(r['counting'])}。数えていない予想：材料を取る前に保存 {r['excluded']['before_material']}件・締切＋10分より後に保存 {r['excluded']['after_limit']}件・"
             f"発走時刻が不明 {r['excluded']['post_unknown']}件</p>",
             f"<p class='warn'>{html.escape(r['judgement'])}</p>",
             "<h2>比較：市場のみ・学習のみ・統合（同じレース・同じ時点）</h2>",
             table(r["comparison"], ["set", "method", "races", "logloss", "hit10", "roi_ev", "bet_races"],
                   ["組", "方式", "対象（レース×時点）", "対数損失（主）", "上位10組", "仮想回収率（期待値配分）", "賭けたレース"]),
             "<p class='muted'>対数損失は小さいほど良い。全組の確率が正式なものだけで計算。8週ごとの点検は --from/--to で期間を指定。"
             "市場推定確率＝発走前の三連複オッズの逆数を合計1に直したもの（比較の基準。的中確率としての正確さは未確認。補正なし）</p>",
             "<h2>仮想の財布</h2>", table(r["wallets"], ["method", "wallet", "races", "bet_races", "stake", "returned", "roi", "hit_races"],
                                      ["方式", "配分", "レース", "賭けたレース", "賭け金", "払戻", "回収率", "的中レース"]),
             "<h2>予想の当たり具合</h2>", table(r["accuracy"], ["method", "time_point", "races", "上位5", "上位10", "上位20"],
                                          ["方式", "時点", "レース", "上位5組", "上位10組", "上位20組"]),
             "<h2>食い違いの印ごとの学習モデル（配分には使わない）</h2>",
             table(r["role_a"], ["mark", "races", "上位5", "上位10", "上位20"], ["印", "レース", "上位5組", "上位10組", "上位20組"]),
             "<h2>ルールの比較版（本採用の判断はあいす）</h2>",
             table(r["rule_variants"], ["method", "races", "総合点が付く馬", "上位10", "上位20"], ["版", "レース", "総合点が付く馬", "上位10組", "上位20組"]),
             "<h2>オッズの合格判定</h2>", table(r["odds_gate"], ["time_point", "checked", "passed", "reasons"], ["時点", "判定", "合格", "不合格の主な理由"]),
             "<h2>収集</h2>", f"<p>取り損ね {r['collection']['missed']} 件</p>"]
    path = out / f"{month}.html"
    path.write_text(_page(f"月次の振り返り {month}", "".join(parts)), encoding="utf-8")
    return path


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, dict):
        return "、".join(f"{k} {n}" for k, n in v.items()) or "–"
    if isinstance(v, float):
        return f"{v * 100:.1f}%" if v <= 1.5 else (f"{v:.3f}" if v < 20 else f"{v:,.0f}")
    return f"{v:,}" if isinstance(v, int) else str(v)
