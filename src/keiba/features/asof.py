"""時点つき特徴を、全出走を日付順に1回なめて作る。

規則（未来の情報を入れないための作り）：
  - 開催日ごとに、まずその日の全出走の特徴を「前日までの状態」だけから作る
  - その後で、その日の結果を状態に足す
  したがって同じ日の他のレース（午前のレースの結果など）も使わない。
  - そのレース自身の着順・タイム・確定オッズ・上りは特徴に入れない（schema.POST_RACE_ONLY）
"""
from __future__ import annotations

import math
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from ..schema import available

CLASS_ORDER = {"新馬": 0, "未勝利": 0, "1勝": 1, "2勝": 2, "3勝": 3, "OP": 4, "L": 4.5, "G3": 5, "G2": 6, "G1": 7}
SURFACE_CODE = {"芝": 0, "ダート": 1, "障害": 2}
GOING_CODE = {"良": 0, "稍重": 1, "重": 2, "不良": 3}
SEX_CODE = {"牡": 0, "牝": 1, "セ": 2, "騸": 2}
HISTORY = 10

# 特徴の列と、使える時点（schema の列に対応づける。過去走から作るものは前日に分かるので 24h）
FEATURES_24H = [
    "age", "sex_code", "weight_carried", "weight_carried_rel", "waku", "horse_number", "field_size",
    "distance_m", "surface_code", "class_code", "venue_code",
    "n_runs", "days_since_last", "last_finish_pct", "ewm_finish_pct", "best_fig5", "mean_fig3", "last_fig",
    "fig_rel_race", "fig_rank_race", "dist_change", "surface_apt", "dist_apt", "venue_apt", "early_pos3",
    "last3f_rel3", "class_change", "jockey_top3", "jockey_win", "jockey_n", "trainer_top3", "trainer_win",
    "trainer_n", "waku_bias", "top3_rate_runs", "win_rate_runs",
]
FEATURES_1H_EXTRA = ["going_code", "going_apt", "body_weight", "body_weight_diff"]


def feature_columns(time_point: str) -> list[str]:
    cols = list(FEATURES_24H)
    if available("going", time_point):
        cols += FEATURES_1H_EXTRA
    return cols


def _shrunk(hits: float, n: float, prior: float, k: float = 20.0) -> float:
    return (hits + prior * k) / (n + k)


def _early_pos(passage, field):
    if not isinstance(passage, str) or not field:
        return np.nan
    first = passage.split("-")[0].strip()
    return int(first) / field if first.isdigit() else np.nan


class _TimeStats:
    """コース・距離ごとの走破時計の平均と標準偏差（足した分だけ）。"""

    def __init__(self):
        self.s = defaultdict(lambda: [0, 0.0, 0.0])

    def figure(self, key_fine, key_coarse, t):
        for key, min_n in ((key_fine, 30), (key_coarse, 30)):
            n, s1, s2 = self.s[key]
            if n >= min_n:
                mean = s1 / n
                sd = math.sqrt(max(s2 / n - mean * mean, 1e-6))
                return (mean - t) / sd
        return np.nan

    def add(self, key, t):
        v = self.s[key]
        v[0] += 1
        v[1] += t
        v[2] += t * t


def build_features(races: pd.DataFrame, runners: pd.DataFrame) -> pd.DataFrame:
    """全出走の特徴表を返す（出走取消・除外は含めない）。"""
    race_cols = ["race_key", "surface", "distance_m", "going", "race_class", "field_size", "venue"]
    df = runners.merge(races[[c for c in race_cols if c in races]].rename(columns={"venue": "venue_r"}),
                       on="race_key", how="left")
    df = df[~df["status"].isin(["withdrawn", "excluded"])].copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    starters = df.groupby("race_key")["horse_number"].transform("count")
    df["field_size"] = df["field_size"].fillna(starters)
    df.loc[df["field_size"] < starters, "field_size"] = starters
    venues = {v: i for i, v in enumerate(sorted(df["venue"].dropna().unique()))}

    horse_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY))
    person = {"jockey": defaultdict(lambda: [0, 0, 0]), "trainer": defaultdict(lambda: [0, 0, 0])}
    waku_stats = defaultdict(lambda: [0, 0])
    tstats = _TimeStats()
    base = {"top3": 3 / 14, "win": 1 / 14}
    out = []

    for date, day in df.sort_values(["race_date", "race_key", "horse_number"]).groupby("race_date", sort=True):
        feats = []
        for rec in day.itertuples(index=False):
            hid = rec.horse_id if isinstance(rec.horse_id, str) else None
            hist = list(horse_hist[hid]) if hid else []
            f = {"runner_key": rec.runner_key, "race_key": rec.race_key, "race_date": date,
                 "horse_number": rec.horse_number, "horse_id": hid}
            f["age"] = rec.age
            f["sex_code"] = SEX_CODE.get(rec.sex, np.nan) if isinstance(rec.sex, str) else np.nan
            f["weight_carried"] = rec.weight_carried
            f["waku"] = rec.waku
            f["field_size"] = rec.field_size
            f["distance_m"] = rec.distance_m
            f["surface_code"] = SURFACE_CODE.get(rec.surface, np.nan)
            f["class_code"] = CLASS_ORDER.get(rec.race_class, np.nan)
            f["venue_code"] = venues.get(rec.venue, np.nan)
            f["going_code"] = GOING_CODE.get(rec.going, np.nan)
            f["body_weight"] = rec.body_weight
            f["body_weight_diff"] = rec.body_weight_diff
            f["n_runs"] = len(hist)
            if hist:
                last = hist[-1]
                f["days_since_last"] = (date - last["date"]).days
                pct = [h["pct"] for h in hist if not math.isnan(h["pct"])]
                f["last_finish_pct"] = last["pct"]
                w = [0.5 ** i for i in range(len(pct))][::-1]
                f["ewm_finish_pct"] = float(np.average(pct, weights=w)) if pct else np.nan
                figs = [h["fig"] for h in hist[-5:] if not math.isnan(h["fig"])]
                f["best_fig5"] = max(figs) if figs else np.nan
                f3 = [h["fig"] for h in hist[-3:] if not math.isnan(h["fig"])]
                f["mean_fig3"] = float(np.mean(f3)) if f3 else np.nan
                f["last_fig"] = last["fig"]
                f["dist_change"] = (rec.distance_m - last["dist"]) if rec.distance_m and last["dist"] else np.nan
                same_s = [h["pct"] for h in hist if h["surface"] == rec.surface and not math.isnan(h["pct"])]
                f["surface_apt"] = float(np.mean(same_s)) if same_s else np.nan
                near = [h["pct"] for h in hist if rec.distance_m and h["dist"] and abs(h["dist"] - rec.distance_m) <= 200 and not math.isnan(h["pct"])]
                f["dist_apt"] = float(np.mean(near)) if near else np.nan
                same_v = [h["pct"] for h in hist if h["venue"] == rec.venue and not math.isnan(h["pct"])]
                f["venue_apt"] = float(np.mean(same_v)) if same_v else np.nan
                heavy = rec.going in ("重", "不良")
                same_g = [h["pct"] for h in hist if (h["going"] in ("重", "不良")) == heavy and h["going"] and not math.isnan(h["pct"])]
                f["going_apt"] = float(np.mean(same_g)) if same_g else np.nan
                ep = [h["early"] for h in hist[-3:] if not math.isnan(h["early"])]
                f["early_pos3"] = float(np.mean(ep)) if ep else np.nan
                l3 = [h["l3rel"] for h in hist[-3:] if not math.isnan(h["l3rel"])]
                f["last3f_rel3"] = float(np.mean(l3)) if l3 else np.nan
                cc = CLASS_ORDER.get(rec.race_class, np.nan)
                f["class_change"] = cc - last["cls"] if not (math.isnan(cc) or math.isnan(last["cls"])) else np.nan
                f["top3_rate_runs"] = float(np.mean([h["pos"] is not None and h["pos"] <= 3 for h in hist]))
                f["win_rate_runs"] = float(np.mean([h["pos"] == 1 for h in hist]))
            for role, key in (("jockey", rec.jockey_key), ("trainer", rec.trainer_key)):
                n, win, top3 = person[role][key] if isinstance(key, str) else (0, 0, 0)
                f[f"{role}_n"] = n
                f[f"{role}_win"] = _shrunk(win, n, base["win"])
                f[f"{role}_top3"] = _shrunk(top3, n, base["top3"])
            wk = (rec.venue, rec.surface, rec.waku)
            wn, wt = waku_stats[wk]
            f["waku_bias"] = _shrunk(wt, wn, base["top3"], k=50) - base["top3"]
            feats.append(f)
        fd = pd.DataFrame(feats)
        for c in FEATURES_24H + FEATURES_1H_EXTRA:
            if c not in fd:
                fd[c] = np.nan
        # レース内の相対値（同じレースの他馬の発走前情報だけを使う）
        g = fd.groupby("race_key")
        fd["weight_carried_rel"] = fd["weight_carried"] - g["weight_carried"].transform("mean")
        fd["fig_rel_race"] = fd["best_fig5"] - g["best_fig5"].transform("median")
        fd["fig_rank_race"] = g["best_fig5"].rank(ascending=False, pct=True)
        out.append(fd)

        # ---- ここから、その日の結果を状態に足す ----
        d2 = day.copy()
        fin = d2[d2["status"] == "finished"]
        race_l3 = fin.groupby("race_key")["last3f_s"].mean()
        figs_today = {}
        for rec in d2.itertuples(index=False):
            if rec.status == "finished" and rec.finish_time_s and rec.distance_m and isinstance(rec.surface, str):
                figs_today[rec.runner_key] = tstats.figure((rec.venue, rec.surface, rec.distance_m),
                                                           (rec.surface, rec.distance_m), rec.finish_time_s)
        for rec in d2.itertuples(index=False):
            if rec.status == "finished" and rec.finish_time_s and rec.distance_m and isinstance(rec.surface, str):
                tstats.add((rec.venue, rec.surface, rec.distance_m), rec.finish_time_s)
                tstats.add((rec.surface, rec.distance_m), rec.finish_time_s)
            pos = rec.finish_position if rec.status == "finished" and pd.notna(rec.finish_position) else None
            field = rec.field_size or np.nan
            pct = (pos - 1) / max(field - 1, 1) if pos else (1.0 if rec.status == "did_not_finish" else np.nan)
            hid = rec.horse_id if isinstance(rec.horse_id, str) else None
            if hid:
                l3 = rec.last3f_s - race_l3.get(rec.race_key, np.nan) if pd.notna(rec.last3f_s) else np.nan
                horse_hist[hid].append({
                    "date": date, "pct": pct, "pos": pos, "fig": float(np.clip(figs_today.get(rec.runner_key, np.nan), -5, 5)),
                    "dist": rec.distance_m if pd.notna(rec.distance_m) else None, "surface": rec.surface,
                    "venue": rec.venue, "going": rec.going if isinstance(rec.going, str) else None,
                    "early": _early_pos(rec.passage_raw, rec.field_size), "l3rel": float(np.clip(l3, -5, 5)),
                    "cls": CLASS_ORDER.get(rec.race_class, np.nan),
                })
            for role, key in (("jockey", rec.jockey_key), ("trainer", rec.trainer_key)):
                if isinstance(key, str):
                    v = person[role][key]
                    v[0] += 1
                    v[1] += pos == 1
                    v[2] += bool(pos and pos <= 3)
            if isinstance(rec.surface, str) and pd.notna(rec.waku):
                ws = waku_stats[(rec.venue, rec.surface, rec.waku)]
                ws[0] += 1
                ws[1] += bool(pos and pos <= 3)

    feat = pd.concat(out, ignore_index=True)
    label_cols = ["runner_key", "status", "finish_position", "final_win_odds", "final_popularity"]
    feat = feat.merge(df[label_cols], on="runner_key", how="left")
    for c in FEATURES_24H + FEATURES_1H_EXTRA:
        if c not in feat:
            feat[c] = np.nan
    return feat
