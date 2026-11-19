"""時間順の検証（ウォークフォワード）と、封印期間の1回だけの評価。

各月 M について：
  学習  ：M の開始より CALIB_DAYS 日前までのレース
  較正  ：その後 M の開始までのレース（β・γ を合わせる）
  予想  ：M のレース
封印期間（holdout_start 以降）は、検証が全部終わってから1回だけ予想して評価する。
予想は全部、共通の形にして台帳に保存し、検証A（オッズを見ない）の参考買い目で精算する。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .features.asof import feature_columns
from .ledger import Ledger, canonical, code_hash, sha
from .models.baseline import LogitModel, PopularityModel
from .models.ranker import RankerModel
from .models.rule import GROUPS, RULE_VERSION, RuleModel, score_groups
from .models import rule_v1
from .models.trio import Calibration, fit_calibration, trio_probs, win_probs
from .settle import settle_trio

CALIB_DAYS = 60
TOP_TRIOS_SAVED = 20
REF_WALLETS = {"ref_top5_equal": 5, "ref_top10_equal": 10}
HIT_K = (1, 5, 10, 20)
METHOD_KIND = {"rule": "rule", "rule_v11": "rule", "rule_v01": "rule", "ml_lgbm": "ml", "base_logit": "baseline", "base_pop": "baseline"}
TIME_POINTS_EVAL = ("24h", "1h")


@dataclass
class EvalConfig:
    first_month: str = "2025-05-01"
    holdout_start: str = "2026-08-16"   # 直近3か月（データの最終日 2026-11-15 から）
    min_field: int = 5
    methods: tuple = ("rule", "rule_v01", "ml_lgbm", "base_logit", "base_pop")
    time_points: tuple = TIME_POINTS_EVAL
    version_label: str = "v0.1"
    run_holdout: bool = True
    last_date: str | None = None   # これより後のレースは使わない（実験で封印期間に触れないため）


def eligible_features(feat: pd.DataFrame, races: pd.DataFrame, min_field: int) -> pd.DataFrame:
    ok = set(races.loc[races["attr_source"].notna(), "race_key"])
    df = feat[feat["race_key"].isin(ok)].copy()
    size = df.groupby("race_key")["horse_number"].transform("count")
    return df[size >= min_field].sort_values(["race_date", "race_key", "horse_number"]).reset_index(drop=True)


def actual_order(g: pd.DataFrame) -> tuple | None:
    """1〜3着の馬番。同着などで決まらなければ None。"""
    fin = g[g["status"] == "finished"]
    out = []
    for pos in (1, 2, 3):
        hit = fin.loc[fin["finish_position"] == pos, "horse_number"]
        if len(hit) != 1:
            return None
        out.append(int(hit.iloc[0]))
    return tuple(out)


def make_model(method: str, tp: str):
    feats = feature_columns(tp)
    return {"rule": lambda: rule_v1.RuleV1Model(tp), "rule_v11": lambda: rule_v1.RuleV1Model(tp, "v1.1"), "rule_v01": lambda: RuleModel(tp), "ml_lgbm": lambda: RankerModel(feats),
            "base_logit": lambda: LogitModel(feats), "base_pop": lambda: PopularityModel()}[method]()


def _calibration_set(df: pd.DataFrame, scores: np.ndarray):
    races = []
    df = df.assign(_s=scores)
    for _, g in df.groupby("race_key", sort=False):
        order = actual_order(g)
        if order is None:
            continue
        nums = g["horse_number"].astype(int).tolist()
        if not all(o in nums for o in order):
            continue
        races.append((g["_s"].to_numpy(dtype=float), tuple(nums.index(o) for o in order)))
    return races


def confidence(top5_mass: float, coverage: float, mean_runs: float) -> str:
    if coverage < 0.5 or mean_runs < 1.0:
        return "Z"
    if top5_mass >= 0.30:
        return "A"
    if top5_mass >= 0.20:
        return "B"
    if top5_mass >= 0.12:
        return "C"
    return "D"


def predict_race(g: pd.DataFrame, scores: np.ndarray, calib: Calibration, method: str, tp: str,
                 version: str, rule_groups: pd.DataFrame | None) -> dict:
    nums = g["horse_number"].astype(int).to_numpy()
    s = calib.strength(scores)
    probs = trio_probs(s, calib.gamma)
    trios = sorted(((tuple(int(nums[i]) for i in c), p) for c, p in probs.items()), key=lambda x: -x[1])
    p_top3 = np.zeros(len(nums))
    for c, p in probs.items():
        p_top3[list(c)] += p
    pw = win_probs(s)
    horses = []
    for i, n in enumerate(nums):
        h = {"n": int(n), "score": None if not np.isfinite(scores[i]) else round(float(scores[i]), 5),
             "p_win": round(float(pw[i]), 5), "p_top3": round(float(p_top3[i]), 5)}
        if rule_groups is not None:
            row = rule_groups.iloc[i]
            h["groups"] = {k: (None if pd.isna(row[k]) else round(float(row[k]), 2)) for k in GROUPS}
            h["coverage"] = round(float(row["rule_coverage"]), 3)
        horses.append(h)
    top5 = sum(p for _, p in trios[:5])
    cov = float(rule_groups["rule_coverage"].mean()) if rule_groups is not None else 1.0
    conf = confidence(top5, cov, float(g["n_runs"].mean()))
    if method in ("rule", "rule_v11"):  # rule-v1.x は被覆で決める（仕様）。レースの代表は被覆の中央値
        conf = rule_v1.coverage_label(float(rule_groups["rule_coverage"].median()),
                                      0 if rule_groups["rule_total"].notna().any() else None)
    notes = []
    if method == "base_pop":
        notes.append("確定単勝オッズを使う比較専用。発走前には作れない")
    if method == "rule_v01":
        notes.append(f"尺度は {RULE_VERSION}（Claude の暫定版。比較用）")
    if method == "rule_v11":
        notes.append("rule-v1.1 の案（未採点を減らし、総合点なしの馬は平均扱い）。offline_proxy")
    if method == "rule":
        notes.append(f"尺度は {rule_v1.RULE_V1_VERSION}（あいす初版）。{rule_v1.MODE}：候補IDと保存済みの結果版で再現（発走前検証ではない）")
    return {
        "race_date": str(g["race_date"].iloc[0].date()), "method_kind": METHOD_KIND[method],
        "uses_post_race_odds": method == "base_pop",
        "horses": horses,
        "trios": [{"combo": "-".join(map(str, sorted(c))), "p": round(p, 6)} for c, p in trios[:TOP_TRIOS_SAVED]],
        "top5_mass": round(top5, 4),
        "confidence": conf,
        "calibration": {"beta": round(calib.beta, 4), "gamma": round(calib.gamma, 4), "n": calib.n_races},
        "allocation": {"status": "unallocated_no_odds", "reason": "発走前の三連複オッズが無い（検証A）"},
        "notes": notes,
    }


def _trio_prob_of(payload: dict, g: pd.DataFrame, calib: Calibration, scores: np.ndarray, order) -> float:
    if order is None:
        return float("nan")
    nums = g["horse_number"].astype(int).tolist()
    probs = trio_probs(calib.strength(scores), calib.gamma)
    key = tuple(sorted(nums.index(o) for o in order))
    return probs.get(key, 0.0)


def run_period(train, calib_df, test, tp, cfg, version, ledger, label, payout_map, scratch_map, importance):
    rows = []
    feats_used = feature_columns(tp)
    for method in cfg.methods:
        model = make_model(method, tp)
        if len(train):
            model.fit(train)
        calib = fit_calibration(_calibration_set(calib_df, model.score(calib_df))) if len(calib_df) else Calibration()
        if method == "ml_lgbm":
            importance[f"{label}:{tp}"] = dict(list(model.importance().items())[:15])
        scores_all = model.score(test)
        rule_all = (rule_v1.score_frame(test, tp) if method == "rule" else
                    rule_v1.score_frame(test, tp, rule_v1.VARIANTS["v1.1"]["prefix"]) if method == "rule_v11" else
                    score_groups(test, tp) if method == "rule_v01" else None)
        model_hash = sha(model.fingerprint() + canonical(calib.__dict__))[:16]
        test = test.assign(_s=scores_all)
        for rk, g in test.groupby("race_key", sort=False):
            sc = g["_s"].to_numpy(dtype=float)
            if method == "base_pop" and not np.isfinite(sc).all():
                continue  # 確定オッズが無いレース（取消など）は比較から外す
            rg = rule_all.loc[g.index] if rule_all is not None else None
            payload = predict_race(g, sc, calib, method, tp, version, rg)
            payload["period"] = label
            inp = sha(canonical(g[["horse_number"] + feats_used].round(6).astype(object).where(g[["horse_number"] + feats_used].notna(), None).values.tolist()))[:16]
            ledger.add_prediction(rk, tp, method, version, payload, inp, model_hash, code_hash())
            order = actual_order(g)
            p_actual = _trio_prob_of(payload, g, calib, sc, order)
            ranked = [t["combo"] for t in payload["trios"]]
            actual = "-".join(map(str, sorted(order))) if order else None
            rec = {"race_key": rk, "race_date": payload["race_date"], "time_point": tp, "method": method,
                   "period": label, "field": len(g), "p_actual": p_actual, "actual": actual,
                   "rank_actual": (ranked.index(actual) + 1) if actual in ranked else None,
                   "confidence": payload["confidence"], "top5_mass": payload["top5_mass"]}
            # 3着以内の確率の較正を見るための材料
            fin_top3 = set(order) if order else set()
            rec["horse_p_top3"] = [(h["p_top3"], int(h["n"] in fin_top3)) for h in payload["horses"]] if order else []
            # 検証Aの参考買い目（オッズを見ずに上位k点を100円ずつ）を精算
            for wallet, k in REF_WALLETS.items():
                stakes = {c: 100 for c in ranked[:k]}
                st = settle_trio(stakes, int((g["status"] == "finished").sum()), scratch_map.get(rk, set()), payout_map.get(rk))
                ledger.add_settlement(rk, tp, method, version, wallet,
                                      {"status": st.status, "stake": st.stake, "returned": st.returned, "hits": st.hits})
                rec[f"{wallet}_status"] = st.status
                rec[f"{wallet}_stake"] = st.stake
                rec[f"{wallet}_return"] = st.returned
            rows.append(rec)
    return rows


def _bootstrap_roi(stake: np.ndarray, ret: np.ndarray, n: int = 2000, seed: int = 7):
    if len(stake) == 0:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(stake), size=(n, len(stake)))
    roi = ret[idx].sum(axis=1) / stake[idx].sum(axis=1)
    return float(np.quantile(roi, 0.025)), float(np.quantile(roi, 0.975))


def summarize(rows: pd.DataFrame) -> list[dict]:
    out = []
    for (period, tp, method), g in rows.groupby(["period", "time_point", "method"]):
        valid = g[g["actual"].notna()]
        p = valid["p_actual"].clip(lower=1e-6)
        uniform = np.log(valid["field"].apply(lambda n: math.comb(int(n), 3)))
        rec = {"period": period, "time_point": tp, "method": method, "races": int(len(g)),
               "races_scored": int(len(valid)),
               "trio_logloss": round(float(-np.log(p).mean()), 4) if len(valid) else None,
               "uniform_logloss": round(float(uniform.mean()), 4) if len(valid) else None,
               "mean_p_actual": round(float(valid["p_actual"].mean()), 5) if len(valid) else None}
        for k in HIT_K:
            rec[f"hit@{k}"] = round(float((valid["rank_actual"].notna() & (valid["rank_actual"] <= k)).mean()), 4) if len(valid) else None
        pairs = [x for lst in valid["horse_p_top3"] for x in lst]
        if pairs:
            pp = np.array(pairs)
            bins = np.clip((pp[:, 0] * 10).astype(int), 0, 9)
            rec["calibration_top3"] = [{"bin": f"{b/10:.1f}-{(b+1)/10:.1f}", "n": int((bins == b).sum()),
                                        "pred": round(float(pp[bins == b, 0].mean()), 3),
                                        "actual": round(float(pp[bins == b, 1].mean()), 3)}
                                       for b in range(10) if (bins == b).sum() > 0]
        for wallet in REF_WALLETS:
            s = g[g[f"{wallet}_status"] == "settled"]
            stake, ret = s[f"{wallet}_stake"].to_numpy(float), s[f"{wallet}_return"].to_numpy(float)
            lo, hi = _bootstrap_roi(stake, ret)
            rec[wallet] = {"races_settled": int(len(s)), "stake": int(stake.sum()), "returned": int(ret.sum()),
                           "roi": round(float(ret.sum() / stake.sum()), 4) if stake.sum() else None,
                           "roi_95ci": [None if lo is None else round(lo, 3), None if hi is None else round(hi, 3)],
                           "hit_races": int((s[f"{wallet}_return"] > 0).sum())}
        conf = g.groupby("confidence").agg(n=("race_key", "count"), hit5=("rank_actual", lambda r: float(((r <= 5) & r.notna()).mean())))
        rec["by_confidence"] = {c: {"n": int(v["n"]), "hit@5": round(v["hit5"], 3)} for c, v in conf.iterrows()}
        out.append(rec)
    return out


def run_evaluation(feat: pd.DataFrame, races: pd.DataFrame, runners: pd.DataFrame, payouts: pd.DataFrame,
                   ledger: Ledger, cfg: EvalConfig = EvalConfig(), log=print) -> dict:
    df = eligible_features(feat, races, cfg.min_field)
    if cfg.last_date:
        df = df[df["race_date"] <= pd.Timestamp(cfg.last_date)]
    version = f"{cfg.version_label}+{code_hash()[:8]}"
    payout_map: dict = {}
    for r in payouts[payouts["bet_type"] == "trio"].itertuples():
        payout_map.setdefault(r.race_key, {})[r.combo] = int(r.payout_yen)
    scratch = runners[runners["status"].isin(["withdrawn", "excluded"])]
    scratch_map = scratch.groupby("race_key")["horse_number"].apply(lambda s: set(int(x) for x in s)).to_dict()
    hold = pd.Timestamp(cfg.holdout_start)
    months = pd.date_range(cfg.first_month, hold, freq="MS")
    rows, importance = [], {}
    for tp in cfg.time_points:
        for m in months:
            end = min(m + pd.offsets.MonthBegin(1), hold)
            cut = m - pd.Timedelta(days=CALIB_DAYS)
            train = df[df["race_date"] < cut]
            calib = df[(df["race_date"] >= cut) & (df["race_date"] < m)]
            test = df[(df["race_date"] >= m) & (df["race_date"] < end)]
            if test.empty:
                continue
            log(f"[{tp}] 検証 {m:%Y-%m}：学習 {train['race_key'].nunique()}R / 較正 {calib['race_key'].nunique()}R / 予想 {test['race_key'].nunique()}R")
            rows += run_period(train, calib, test, tp, cfg, version, ledger, "walk_forward", payout_map, scratch_map, importance)
            ledger.commit()
    # 封印期間：検証が全部終わってから1回だけ
    for tp in (cfg.time_points if cfg.run_holdout else ()):
        cut = hold - pd.Timedelta(days=CALIB_DAYS)
        train = df[df["race_date"] < cut]
        calib = df[(df["race_date"] >= cut) & (df["race_date"] < hold)]
        test = df[df["race_date"] >= hold]
        if test.empty:
            continue
        log(f"[{tp}] 封印期間：学習 {train['race_key'].nunique()}R / 較正 {calib['race_key'].nunique()}R / 予想 {test['race_key'].nunique()}R")
        rows += run_period(train, calib, test, tp, cfg, version, ledger, "holdout", payout_map, scratch_map, importance)
        ledger.commit()
    rows_df = pd.DataFrame(rows)
    summary = summarize(rows_df)
    result = {"version": version, "config": cfg.__dict__, "summary": summary, "importance": importance,
              "races_eligible": int(df["race_key"].nunique()),
              "period": [str(df["race_date"].min().date()), str(df["race_date"].max().date())]}
    ledger.add_run(version, result)
    ledger.commit()
    return result, rows_df
