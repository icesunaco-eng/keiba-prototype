"""これからのレースを、集めた発走前のページで予想し、配分して、レース後に精算する（仮想の財布。実購入はしない）。

  予想の時点：24h・1h・15m（出馬表は、その時点までに取った一番新しいもの）
  予想の方式：ml_lgbm（学習モデル）・ml_market（学習＋その時点の単勝オッズ）・rule（あいす v1.0）・rule_v11（案）
  配分：三連複オッズが読めたときだけ。equal_payout・ev_weighted・kelly の3つの財布（方式×配分ごとに別の財布）
  精算：15m の配分を使う（無ければ 1h）。設計どおり、15m で変えてよいのは配分・削除・見送りだけ
  台帳の版は "live-<コードのハッシュ>"。過去の検証の版とは混ぜない

オッズの読取は、最初の実物で確かめるまで自己照合（頭数・組の数）が合ったものだけ使う。
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .. import evaluate as ev
from ..allocate import allocate
from ..features.asof import build_features
from ..ledger import Ledger, canonical, code_hash, sha
from ..models import rule_v1
from ..models.trio import fit_calibration
from ..settle import settle_trio
from . import jra

LIVE_METHODS = ("ml_lgbm", "ml_market", "rule", "rule_v11")
ALLOC_METHODS = ("equal_payout", "ev_weighted", "kelly")
TP_ORDER = {"24h": 0, "1h": 1, "15m": 2}


def _snapshots(root: Path) -> pd.DataFrame:
    f = root / "snapshots.jsonl"
    if not f.exists():
        return pd.DataFrame(columns=["race_key", "kind", "time_point", "path", "sha256", "fetched_at"])
    return pd.DataFrame([json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()])


def _read(root: Path, rec) -> str:
    return (root / rec["path"]).read_text(encoding="utf-8")


def live_tables(root: Path, state: dict, snaps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """集めたページから、正規の表と同じ形の races / runners と、時点ごとの入力を作る。
    結果が取れたレースは確定行（過去走として使う）、まだのレースは pending 行。"""
    races, runners, inputs = [], [], {}
    for rk, r in state.get("races", {}).items():
        s = snaps[(snaps["race_key"] == rk)] if len(snaps) else snaps
        races.append({"race_key": rk, "race_date": r["race_date"], "venue": r["venue"], "race_number": r["race_number"],
                      "surface": r.get("surface"), "distance_m": r.get("distance_m"), "going": None, "race_class": None,
                      "field_size": r.get("field_size"), "attr_source": "jra_live", "race_type": "障害候補" if r.get("surface") == "障害" else "平地候補"})
        res = s[s["kind"] == "result"] if len(s) else s
        if len(res):
            parsed = jra.parse_result(_read(root, res.iloc[-1]))
            card = {}
            cards = s[s["kind"] == "card"]
            if len(cards):
                card = {c["horse_number"]: c for c in jra.parse_card(_read(root, cards.iloc[-1]))}
            for row in parsed["rows"]:
                c = card.get(row["horse_number"], {})
                runners.append(_runner(rk, r, {**c, **{k: v for k, v in row.items() if v is not None}}, row["status"]))
            inputs[(rk, "result")] = parsed
            continue
        for tp in ("24h", "1h", "15m"):
            cards = s[(s["kind"] == "card") & s["time_point"].map(lambda x: TP_ORDER.get(x, 9) <= TP_ORDER[tp])] if len(s) else s
            if not len(cards):
                continue
            card = [c for c in jra.parse_card(_read(root, cards.iloc[-1])) if c["horse_number"]]
            if len(card) < 3:
                continue
            field = len(card)
            win = s[(s["kind"].isin(["odds_win", "odds_first"])) & (s["time_point"] == tp)]
            trio = s[(s["kind"] == "odds_trio") & (s["time_point"] == tp)]
            if tp == "15m" and not (len(win) or len(trio)):
                continue  # 15分前は配分の見直しだけ。新しいオッズが無ければ作らない
            w = jra.parse_win_odds(_read(root, win.iloc[-1]), field) if len(win) else {"ok": False, "odds": {}}
            t = jra.parse_trio_odds(_read(root, trio.iloc[-1]), field) if len(trio) else {"ok": False, "odds": {}}
            inputs[(rk, tp)] = {"card": card, "win": w, "trio": t,
                                "snapshots": [x for x in (cards.iloc[-1]["sha256"], *(win["sha256"].tolist()[-1:]), *(trio["sha256"].tolist()[-1:]))]}
        last = max((tp for tp in ("24h", "1h", "15m") if (rk, tp) in inputs), key=TP_ORDER.get, default=None)
        if last:
            for c in inputs[(rk, last)]["card"]:
                runners.append(_runner(rk, r, c, "pending"))
    return pd.DataFrame(races), pd.DataFrame(runners), inputs


def _runner(rk, r, c, status):
    return {"runner_key": f"{rk}|{c['horse_number']}", "race_key": rk, "race_date": r["race_date"],
            "horse_number": c["horse_number"], "horse_id": c.get("horse_id"), "horse_name": c.get("horse_name"),
            "status": status, "counts_as_starter": status != "withdrawn", "finish_position": c.get("finish_position"),
            "dead_heat": False, "finish_time_s": c.get("finish_time_s"), "waku": c.get("waku"), "sex": c.get("sex"),
            "age": c.get("age"), "weight_carried": c.get("weight_carried"), "jockey_key": c.get("jockey_key"),
            "trainer_key": c.get("trainer_key"), "body_weight": c.get("body_weight"), "body_weight_diff": c.get("body_weight_diff"),
            "final_win_odds": None, "final_popularity": c.get("final_popularity"), "last3f_s": c.get("last3f_s"),
            "passage_raw": c.get("passage_raw"), "margin_raw": None, "attr_source": "jra_live", "venue": r["venue"],
            "race_number": r["race_number"], "owner": "live"}


def _models(paths, feat_hist: pd.DataFrame, tp: str, day: str):
    """学習モデルと市場組み合わせを、その日までのデータで1日1回学習してキャッシュする。"""
    cache = paths.data_dir / "live" / "models" / f"{day}-{tp}-{code_hash()[:8]}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cut = pd.Timestamp(day) - pd.Timedelta(days=ev.CALIB_DAYS)
    train, calib = feat_hist[feat_hist["race_date"] < cut], feat_hist[feat_hist["race_date"] >= cut]
    out = {}
    for m in LIVE_METHODS:
        model = ev.make_model(m, tp)
        model.fit(train)
        if hasattr(model, "fit_blend"):
            model.fit_blend(calib, ev._calibration_set)
        out[m] = (model, fit_calibration(ev._calibration_set(calib, model.score(calib))))
    cache.write_bytes(pickle.dumps(out))
    return out


def run_live(paths, log=print) -> dict:
    root = paths.data_dir / "live"
    state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"races": {}}
    snaps = _snapshots(root)
    lr, lrun, inputs = live_tables(root, state, snaps)
    if lrun.empty:
        return {"predicted": 0, "settled": 0}
    races = pd.read_parquet(paths.tables_dir / "races.parquet")
    runners = pd.read_parquet(paths.tables_dir / "runners.parquet")
    lr = lr[~lr["race_key"].isin(races["race_key"])]
    lrun = lrun[~lrun["runner_key"].isin(runners["runner_key"])]
    all_races = pd.concat([races, lr], ignore_index=True)
    all_run = pd.concat([runners, lrun], ignore_index=True)
    feat = build_features(all_races, all_run)
    feat = feat.merge(rule_v1.build_utilities(all_races, all_run), on=["runner_key", "race_key"], how="left")
    feat = feat.merge(rule_v1.build_utilities(all_races, all_run, "v1.1").drop(columns=["is_flat"]), on=["runner_key", "race_key"], how="left")
    hist = ev.eligible_features(feat[~feat["race_key"].isin(lrun.loc[lrun["status"] == "pending", "race_key"])], all_races, 5)
    version = f"live-{code_hash()[:8]}"
    led = Ledger(paths.ledger_path)
    done = {(p["race_key"], p["time_point"], p["method"]) for p in led.predictions(version)}
    n_pred = n_set = 0
    for (rk, tp), inp in sorted(inputs.items()):
        if tp == "result":
            continue
        g = feat[feat["race_key"] == rk].sort_values("horse_number")
        if len(g) < 3:
            continue
        g = g.assign(live_win_odds=g["horse_number"].map(inp["win"]["odds"]) if inp["win"]["ok"] else np.nan)
        models = _models(paths, hist, "1h" if tp == "15m" else tp, str(g["race_date"].iloc[0].date()))
        for m in LIVE_METHODS:
            if (rk, tp, m) in done:
                continue
            model, cal = models[m]
            if m == "ml_market":
                if not inp["win"]["ok"]:
                    continue
                model.odds_col = "live_win_odds"
            sc = np.asarray(model.score(g), dtype=float)
            rg = (rule_v1.score_frame(g, tp) if m == "rule" else
                  rule_v1.score_frame(g, tp, rule_v1.VARIANTS["v1.1"]["prefix"]) if m == "rule_v11" else None)
            payload = ev.predict_race(g, sc, cal, m, tp, version, rg)
            nums = g["horse_number"].astype(int).tolist()
            probs = {"-".join(map(str, sorted(nums[i] for i in c))): p
                     for c, p in ev.trio_probs(cal.strength(sc), cal.gamma).items()}
            odds = inp["trio"]["odds"] if inp["trio"]["ok"] else None
            payload["allocation"] = {a: (lambda x: {"status": x.status, "stakes": x.stakes, "total": x.total})(
                allocate(probs, odds, a, calibrated=cal.n_races >= 30)) for a in ALLOC_METHODS}
            payload["inputs"] = {"snapshots": inp["snapshots"], "win_odds_ok": inp["win"]["ok"], "trio_odds_ok": inp["trio"]["ok"]}
            payload["period"] = "live"
            led.add_prediction(rk, tp, m, version, payload, sha(canonical(inp["snapshots"]))[:16],
                               sha(model.fingerprint() + canonical(cal.__dict__))[:16], code_hash())
            n_pred += 1
    # 精算
    preds = {(p["race_key"], p["time_point"], p["method"]): p for p in led.predictions(version)}
    settled = {(s["race_key"], s["method"], s["wallet"]) for s in led.settlements(version)}
    for (rk, tp), res in inputs.items():
        if tp != "result":
            continue
        rows = res["rows"]
        finishers = sum(r["status"] == "finished" for r in rows)
        scratched = {r["horse_number"] for r in rows if r["status"] in ("withdrawn", "excluded")}
        payouts = {p["combo"]: p["payout_yen"] for p in res["trio_payouts"]} or None
        for m in LIVE_METHODS:
            p = preds.get((rk, "15m", m)) or preds.get((rk, "1h", m))
            if not p:
                continue
            for a in ALLOC_METHODS:
                if (rk, m, a) in settled:
                    continue
                stakes = p["allocation"][a]["stakes"]
                st = settle_trio(stakes, finishers, scratched, payouts) if stakes else None
                led.add_settlement(rk, p["time_point"], m, version, a,
                                   {"status": st.status if st else p["allocation"][a]["status"],
                                    "stake": st.stake if st else 0, "returned": st.returned if st else 0,
                                    "hits": st.hits if st else []})
                n_set += 1
    led.commit()
    led.close()
    return {"predicted": n_pred, "settled": n_set, "version": version}


def wallet_summary(paths) -> pd.DataFrame:
    led = Ledger(paths.ledger_path, read_only=True)
    rows = [s for s in led.settlements() if s["version"].startswith("live-")]
    led.close()
    if not rows:
        return pd.DataFrame(columns=["method", "wallet", "races", "bet_races", "stake", "returned", "roi"])
    df = pd.DataFrame(rows)
    out = df.groupby(["method", "wallet"]).agg(races=("race_key", "nunique"), bet_races=("stake", lambda s: int((s > 0).sum())),
                                               stake=("stake", "sum"), returned=("returned", "sum")).reset_index()
    out["roi"] = (out["returned"] / out["stake"].where(out["stake"] > 0)).round(4)
    return out
