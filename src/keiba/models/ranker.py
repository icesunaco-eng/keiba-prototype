"""学習モデル方式：LightGBM の2つのモデルの平均。

  1. LambdaRank でレース内の順位を学ぶ（目的の値は 1着=3, 2着=2, 3着=1, それ以外=0）
  2. 「3着以内か」の分類
スコアは、それぞれを学習データ上の平均・標準偏差で標準化して足したもの。
設定は 2025-09〜2026-03 の検証月で比べて決めた（木を小さく・回数を多く。封印期間は使っていない）。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

COMMON = dict(subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
              random_state=17, deterministic=True, force_col_wise=True, verbose=-1, n_jobs=4)
PARAMS = dict(objective="lambdarank", n_estimators=800, learning_rate=0.02, num_leaves=15,
              min_child_samples=80, lambdarank_truncation_level=6, label_gain=[0, 1, 3, 7], **COMMON)
PARAMS_BINARY = dict(objective="binary", n_estimators=600, learning_rate=0.02, num_leaves=15,
                     min_child_samples=100, **COMMON)


def relevance(finish_position: pd.Series, status: pd.Series) -> np.ndarray:
    pos = pd.to_numeric(finish_position, errors="coerce").where(status == "finished")
    return np.select([pos == 1, pos == 2, pos == 3], [3, 2, 1], 0)


class RankerModel:
    name = "ml_lgbm"

    def __init__(self, features: list[str]):
        self.features = features
        self.model = None
        self.binary = None
        self.norm = None

    def fit(self, df: pd.DataFrame) -> "RankerModel":
        import lightgbm as lgb

        df = df.sort_values(["race_key", "horse_number"])
        x = df[self.features].astype(float)
        groups = df.groupby("race_key", sort=False).size().to_numpy()
        self.model = lgb.LGBMRanker(**PARAMS)
        self.model.fit(x, relevance(df["finish_position"], df["status"]), group=groups)
        pos = pd.to_numeric(df["finish_position"], errors="coerce").where(df["status"] == "finished")
        self.binary = lgb.LGBMClassifier(**PARAMS_BINARY)
        self.binary.fit(x, (pos <= 3).astype(int))
        a, b = self.model.predict(x), self.binary.predict(x, raw_score=True)
        self.norm = (a.mean(), a.std() + 1e-9, b.mean(), b.std() + 1e-9)
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        x = df[self.features].astype(float)
        ma, sa, mb, sb = self.norm
        return (self.model.predict(x) - ma) / sa + (self.binary.predict(x, raw_score=True) - mb) / sb

    def fingerprint(self) -> str:
        if not self.model:
            return hashlib.sha256(json.dumps(self.features).encode()).hexdigest()[:16]
        text = self.model.booster_.model_to_string() + self.binary.booster_.model_to_string()
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def importance(self) -> dict:
        gain = self.model.booster_.feature_importance("gain")
        return dict(sorted(zip(self.features, map(float, gain)), key=lambda x: -x[1]))
