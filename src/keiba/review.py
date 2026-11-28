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
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from . import official
from .ledger import Ledger
from .settle import winning_combos
from .live.collector import ARCHIVE_FILE, read_jsonl
from .live import wallet
from .live.wallet import _snapshots, live_tables, odds_rows, result_ready
from .report.html import _page

HIT_K = (5, 10, 20)
MARKS = ("食い違い", "食い違いなし", "判定できず")


def _results(paths) -> tuple:
    """（レース → 実際の結果, 予定表, 入力, レースの表, 原本を読めなかったレース → 理由）。レースの表は予定表と予定表から外したレース（races_archive）を重ねた
    読むだけの表（同じレースは予定表を優先）。月次のオッズの判定も、この同じ入力から作る（予定表から外れても過去の判定を消さない）。"""
    root = paths.data_dir / "live"
    state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"races": {}}
    races = {}
    for x in read_jsonl(root / ARCHIVE_FILE)[0]:   # 予定表から外した（7日より前の）レースの結果も数える
        if all(x.get(k) is not None for k in ("race_key", "race_date", "venue", "race_number")):
            races[x["race_key"]] = {k: v for k, v in x.items() if k != "race_key"}
    races.update(state.get("races", {}))
    held: dict = {}
    _, _, inputs = live_tables(root, {**state, "races": races}, _snapshots(root), held)
    # 原本を読めなかったレース（live_tables はそのレースだけ飛ばし、held にレースの鍵で理由を残す）。結果がまだ公表されて
    # いないレースとは分ける（月次はこのレースの月を確定しない。build_review）
    unread = {rk: why for rk, why in held.items() if rk.count("|") == 2}
    out = {}
    for (rk, tp), res in inputs.items():
        if tp != "result":
            continue
        if not result_ready(res, (races.get(rk) or {}).get("field_size")):
            continue   # 公表の途中・一部しか読めていない結果は的中の集計に使わない（仮想財布の精算と同じ判定）
        finished = [(r["horse_number"], r["finish_position"]) for r in res["rows"] if r["status"] == "finished"]
        wins = sorted(winning_combos(finished))   # 3着同着なら的中の組は複数（仮想財布の精算と同じ決め方）
        out[rk] = {"actual": wins[0] if wins else None, "actuals": wins, "payouts": res["trio_payouts"]}
    return out, state, inputs, races, unread


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
                actuals = results[rk].get("actuals") or [results[rk]["actual"]]   # 同着で複数あれば、どれかが来れば当たり（確率は足す）
                ll.append(-np.log(max(sum(p["trio_probs_full"].get(a, 0.0) for a in actuals), 1e-6)))
                hit.append(bool(set(actuals) & {t["combo"] for t in p["trios"][:10]}))
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
    results, state, inputs, races, unread = _results(paths)
    bad = sorted(rk for rk in unread if _in_range(rk[:10], month, start, end))
    if bad:
        # その月のレースの原本を読めない（一時的に無い・壊れている）。読めた分だけで月次を作ると、結果・的中・比較を落とした
        # 中身で上書きし、目印まで今の数になって戻らない。書かずに例外にして、次の見回りでもう一度作る
        raise ReviewUnreadable(f"{len(bad)}レースの原本を読めない（{bad[0]}：{unread[bad[0]]}）"[:300])
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
        if p.get("formal_probability") is False:   # 正式な確率にならなかった統合（参考表示）は的中率に入れない（アプリと同じ）
            continue
        ranked = [t["combo"] for t in p["trios"]]
        ranks = [ranked.index(a) + 1 for a in (res.get("actuals") or [res["actual"]]) if a in ranked]
        rank = min(ranks) if ranks else None   # 同着で的中の組が複数なら、いちばん上の順位
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
    oc = odds_rows(inputs, races, wallet.prediction_clock())   # 予定表から外したレースも（odds_check は今の予定表だけ）
    oc = oc[oc["race_key"].map(lambda k: _in_range(k[:10], month, start, end))] if len(oc) else oc
    review["odds_gate"] = [{"time_point": tp, "checked": len(d), "passed": int(d["pass"].sum()),
                            "reasons": dict(Counter(r for x in d["reasons"] if x for r in x.split(" / ")).most_common(5))}
                           for tp, d in oc.groupby("time_point")] if len(oc) else []

    # 6. 収集
    missed = [k for k in state.get("missed_notified", []) if _in_range(k[:10], month, start, end)]
    review["collection"] = {"missed": len(missed), "missed_examples": missed[:10]}
    return review


# 月次の作り方の版。作り方を変えたら上げる（目印が変わるので、前の作り方で書いた月次は次の見回りで書き直す）
# 2：オッズの判定に、予定表から外したレースも入れる（前の作り方は、外れたレースの判定を月次から落としていた）
REVIEW_FORMAT = 2
MARKS_FILE = ".marks.json"   # data/review/ の中。月 → 月次を書いたときの、その月の月次が読むものの数（month_marks）


def settlement_counts(paths) -> dict[str, int]:
    """月（レースの日付）→ これからのレースの精算の行数（台帳は追記だけなので、増えたら月次が古い）。読めなければ空。"""
    import sqlite3
    if not paths.ledger_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{paths.ledger_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT substr(race_key, 1, 7), COUNT(*) FROM settlements "
                            "WHERE version LIKE 'live-%' OR method='text_ai' GROUP BY 1").fetchall()
    finally:
        conn.close()
    return {m: n for m, n in rows if isinstance(m, str)}


def month_marks(paths) -> dict[str, str]:
    """月（レースの日付）→ その月の月次が読むものの目印。台帳の予想・精算の行数（追記だけ）、収集の目録・発走時刻の記録・
    取りやめ・予定表から外したレースの記録の行数（追記だけ）、予定表の発走時刻・頭数。どれかが変われば月次が古い
    （予想だけ・取得だけ増えた回も書き直す。何も変わらない回は書き直さない）。台帳を読めなければ例外（書き直しの判定をしない）。"""
    import hashlib
    import sqlite3
    from .live.collector import ARCHIVE_FILE, CANCEL_FILE, POST_FILE

    parts: dict[str, dict] = {}

    def add(month, key, n=1):
        if isinstance(month, str) and len(month) == 7:
            d = parts.setdefault(month, {})
            d[key] = d.get(key, 0) + n

    if paths.ledger_path.exists():
        conn = sqlite3.connect(f"file:{paths.ledger_path}?mode=ro", uri=True)
        try:
            for table, key, where in (("settlements", "s", "version LIKE 'live-%' OR method='text_ai'"),
                                      ("predictions", "p", "version LIKE 'live-%'")):
                for m, n in conn.execute(f"SELECT substr(race_key, 1, 7), COUNT(*) FROM {table} WHERE {where} GROUP BY 1"):
                    add(m, key, n)
        finally:
            conn.close()
    root = paths.data_dir / "live"
    for name, key in (("snapshots.jsonl", "c"), (POST_FILE, "h"), (CANCEL_FILE, "x"), (ARCHIVE_FILE, "a")):
        try:
            with open(root / name, encoding="utf-8", errors="replace") as f:
                for line in f:   # 月はレースの鍵（取りやめの記録はレースの日付）の先頭7文字
                    i = line.find('"race_key": "')
                    j = line.find('"race_date": "') if i < 0 else -1
                    add(line[i + 13:i + 20] if i >= 0 else (line[j + 14:j + 21] if j >= 0 else None), key)
        except OSError:
            pass
    try:
        races = json.loads((root / "state.json").read_text(encoding="utf-8")).get("races", {})
    except (OSError, ValueError):
        races = {}
    seen: dict[str, list] = {}
    for rk, r in sorted(races.items()):
        if isinstance(rk, str) and isinstance(r, dict):
            seen.setdefault(rk[:7], []).append((rk, r.get("post_time"), r.get("field_size")))
    out = {}
    for m in sorted(set(parts) | set(seen)):
        d = parts.get(m, {})
        races_hash = hashlib.sha256(json.dumps(seen.get(m, []), default=str).encode()).hexdigest()[:12]
        out[m] = f"v={REVIEW_FORMAT};" + ";".join(f"{k}={d.get(k, 0)}" for k in "spchxa") + f";r={races_hash}"
    return out


def _marks(paths) -> dict:
    try:
        x = json.loads((paths.data_dir / "review" / MARKS_FILE).read_text(encoding="utf-8"))
        return x if isinstance(x, dict) else {}
    except (OSError, ValueError):
        return {}


def stale_months(paths) -> list[str]:
    """月次が読むもの（予想・精算・収集の目録・発走時刻・取りやめ・予定表）が変わったのに、月次がまだ書き直されていない月
    （精算・予想の直後に止まった・月次の書き込みに失敗した・手で動かす月次が書いていて待たなかった等）。
    月次は書き終えてから目印を書くので、どこで止まっても、次の見回りでこの関数が見つけて書き直せる。"""
    marks = _marks(paths)
    return sorted(m for m, n in month_marks(paths).items() if marks.get(m) != n)


def _replace_text(path: Path, text: str):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class ReviewUnreadable(OSError):
    """その月のレースの原本を読めなかったので、月次を書かなかった（JSON・HTML・目印はそのまま。次の見回りで作り直す）。"""


class ReviewBusy(BlockingIOError):
    """ほかの出力（手で動かす keiba review など）が月次を書いている間で、待たずに戻った。"""


def write_review(paths, month: str | None, start: str | None = None, end: str | None = None, wait: bool = True) -> Path:
    """月次（または期間）の振り返りを書く。wait=False なら、ほかの出力が書いている間は待たずに ReviewBusy を上げる
    （見回りは収集の鍵を持ったまま待たない。書けなかった月は review_months に残して次の見回りで書く）。"""
    import fcntl
    out = paths.data_dir / "review"
    out.mkdir(parents=True, exist_ok=True)
    # 月次は1つずつ書く（見回りと手で動かす keiba review が重なると、古い中身が後から上書きし、目印だけ新しくなることがある）
    with open(out / ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ReviewBusy(*exc.args) from exc
        count = month_marks(paths).get(month) if month else None   # 先に数える（作る途中に増えた分は、次の回に書き直す）
        r = build_review(paths, month, start, end)
        path = _write_review_files(out, month or f"{start}_{end}", r)
        if month:
            marks = _marks(paths)
            marks[month] = count
            _replace_text(out / MARKS_FILE, json.dumps(marks, ensure_ascii=False, indent=1))   # 書き終えてから目印
    return path


def _write_review_files(out: Path, month: str, r: dict) -> Path:
    _replace_text(out / f"{month}.json", json.dumps(r, ensure_ascii=False, indent=1, default=str))

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
    _replace_text(path, _page(f"月次の振り返り {month}", "".join(parts)))
    return path


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, dict):
        return "、".join(f"{k} {n}" for k, n in v.items()) or "–"
    if isinstance(v, float):
        return f"{v * 100:.1f}%" if v <= 1.5 else (f"{v:.3f}" if v < 20 else f"{v:,.0f}")
    return f"{v:,}" if isinstance(v, int) else str(v)
