"""市場と学習モデルの組み合わせ（ml_market）。

点 = log(市場の勝率) + v × 学習モデルの点
  市場の勝率 = (1/単勝オッズ) をレース内で足して1にしたもの
  v は較正期間で、実際の1〜3着の並びの尤度が一番高くなる値を選ぶ（0 なら市場だけと同じ）
v が 0 より大きく、組み合わせが市場だけ（base_pop）より当たるなら、学習モデルは市場に無い情報を持っている。

過去データには発走前のオッズが無いので、検証では確定単勝オッズで近似する（結果に明記）。
これからのレースでは、収集した発走前（1時間前・15分前）の単勝オッズを使う。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .ranker import RankerModel
from .trio import _neg_loglik, fit_calibration

V_GRID = [0.0, 0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0]


def market_logp(df: pd.DataFrame, odds_col: str = "final_win_odds") -> pd.Series:
    inv = 1 / pd.to_numeric(df[odds_col], errors="coerce").where(lambda x: x > 0)
    p = inv / inv.groupby(df["race_key"]).transform("sum")
    lp = np.log(p)
    return lp.fillna(lp.groupby(df["race_key"]).transform("min")).fillna(0.0)


class MarketBlendModel:
    name = "ml_market"

    def __init__(self, features: list[str], odds_col: str = "final_win_odds"):
        self.ml = RankerModel(features)
        self.odds_col = odds_col
        self.w = 0.0  # 学習モデルの重み v

    def fit(self, df):
        self.ml.fit(df)
        return self

    def _parts(self, df):
        return self.ml.score(df), market_logp(df, self.odds_col).to_numpy()

    def fit_blend(self, calib_df: pd.DataFrame, calibration_set):
        """calibration_set(df, scores) → [(scores, order)]（evaluate の関数を渡す）。"""
        ml, mk = self._parts(calib_df)
        best = (np.inf, 0.0)
        for w in V_GRID:
            races = calibration_set(calib_df, mk + w * ml)
            cal = fit_calibration(races)
            nll = _neg_loglik([cal.beta, np.log(cal.gamma)], races) / max(len(races), 1)
            best = min(best, (nll, w))
        self.w = best[1]
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        ml, mk = self._parts(df)
        return mk + self.w * ml

    def fingerprint(self) -> str:
        return f"{self.ml.fingerprint()}:v={self.w}:{self.odds_col}"

    def importance(self) -> dict:
        return self.ml.importance()
