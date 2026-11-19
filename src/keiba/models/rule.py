"""ルール方式：8グループを10段階で採点する（決定的。学習しない）。

尺度は「あいす叩き台・暫定版」。版番号 RULE_VERSION を付け、後から差し替えられるようにする。
各グループは、同じレースの中での順位を 1〜10点にしたもの。材料が無い馬は「未採点」（NaN）で、
合計（平均点）の計算から外す。採点できたグループの割合を coverage として残す。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RULE_VERSION = "rule-v0.1-provisional"

# グループ → [(特徴, 大きいほど良いなら +1 / 小さいほど良いなら -1)]
GROUPS: dict[str, list[tuple[str, int]]] = {
    "ability": [("best_fig5", +1), ("mean_fig3", +1)],
    "recent": [("ewm_finish_pct", -1), ("last_finish_pct", -1)],
    "distance": [("dist_apt", -1)],
    "course": [("venue_apt", -1), ("surface_apt", -1)],
    "going": [("going_apt", -1)],
    "pace": [("last3f_rel3", -1), ("early_pos3", -1)],
    "draw": [("waku_bias", +1)],
    "weight_rider": [("jockey_top3", +1), ("weight_carried_rel", -1)],
}
GROUP_LABELS = {"ability": "能力", "recent": "近走", "distance": "距離", "course": "コース", "going": "馬場",
                "pace": "展開・脚", "draw": "枠", "weight_rider": "斤量・騎手"}


def _points(values: pd.Series, race: pd.Series, sign: int) -> pd.Series:
    v = values * sign
    pct = v.groupby(race).rank(pct=True, method="average")
    return (1 + np.floor(pct * 9.999)).where(v.notna())


def score_groups(df: pd.DataFrame, time_point: str) -> pd.DataFrame:
    """馬ごとの各グループ点（1〜10、未採点は NaN）、平均点、採点割合。"""
    out = pd.DataFrame(index=df.index)
    for group, items in GROUPS.items():
        parts = []
        for feat, sign in items:
            if feat == "going_apt" and time_point == "24h":
                continue  # 馬場は当日にならないと分からない
            if feat in df:
                parts.append(_points(df[feat].astype(float), df["race_key"], sign))
        out[group] = pd.concat(parts, axis=1).mean(axis=1) if parts else np.nan
    out["rule_total"] = out[list(GROUPS)].mean(axis=1)
    out["rule_coverage"] = out[list(GROUPS)].notna().sum(axis=1) / len(GROUPS)
    return out


class RuleModel:
    name = "rule"

    def __init__(self, time_point: str):
        self.time_point = time_point

    def fit(self, df):
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return score_groups(df, self.time_point)["rule_total"].to_numpy()

    def fingerprint(self) -> str:
        return RULE_VERSION
