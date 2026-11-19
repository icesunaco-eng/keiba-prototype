"""比較用の基準モデル。

base_logit：ロジスティック回帰（3着以内か）。特徴は学習モデルと同じ。
base_pop  ：確定単勝オッズの逆数（人気順）。発走後の値なので、予想には使えない。
            「市場の見方と比べてどうか」を見るための比較専用。
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


class LogitModel:
    name = "base_logit"

    def __init__(self, features: list[str]):
        self.features = features
        self.model = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                                   LogisticRegression(C=0.5, max_iter=500))

    def fit(self, df: pd.DataFrame) -> "LogitModel":
        pos = pd.to_numeric(df["finish_position"], errors="coerce").where(df["status"] == "finished")
        self.model.fit(df[self.features].astype(float), (pos <= 3).astype(int))
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self.model.decision_function(df[self.features].astype(float))

    def fingerprint(self) -> str:
        coef = self.model[-1].coef_.round(6).tobytes()
        return hashlib.sha256(coef).hexdigest()[:16]


class PopularityModel:
    name = "base_pop"

    def fit(self, df):
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        odds = pd.to_numeric(df["final_win_odds"], errors="coerce")
        return -np.log(odds.where(odds > 0)).to_numpy()

    def fingerprint(self) -> str:
        return "final-odds"
