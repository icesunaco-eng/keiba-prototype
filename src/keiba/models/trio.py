"""強さ（スコア）から三連複の確率を出す。

着順モデル（Plackett–Luce）に、2・3着の確率を平らにする補正（Henery型の指数 γ）を入れる：
  P(i→j→k) = s_i/Σs × s_j^γ/(Σs^γ − s_i^γ) × s_k^γ/(Σs^γ − s_i^γ − s_j^γ)
  三連複 {i,j,k} の確率 = 6通りの並びの和
スコア x から強さ s = exp(β·x) にする β と、γ は、較正用の期間の「実際の1〜3着の並び」の尤度を最大にして決める。
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

import numpy as np
from scipy.optimize import minimize

_PERM_CACHE: dict[int, np.ndarray] = {}


def _perms(n: int) -> np.ndarray:
    if n not in _PERM_CACHE:
        _PERM_CACHE[n] = np.array(list(permutations(range(n), 3)), dtype=np.int16).reshape(-1, 3)
    return _PERM_CACHE[n]


def ordered_top3_probs(strength: np.ndarray, gamma: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """全ての (1着,2着,3着) の並びと確率。"""
    s = np.asarray(strength, dtype=float)
    n = len(s)
    if n < 3:
        return np.empty((0, 3), dtype=np.int16), np.empty(0)
    p = _perms(n)
    sg = s ** gamma
    tot, totg = s.sum(), sg.sum()
    i, j, k = p[:, 0], p[:, 1], p[:, 2]
    prob = (s[i] / tot) * (sg[j] / np.maximum(totg - sg[i], 1e-300)) * (sg[k] / np.maximum(totg - sg[i] - sg[j], 1e-300))
    return p, prob


def trio_probs(strength: np.ndarray, gamma: float = 1.0) -> dict[tuple[int, int, int], float]:
    """三連複（順不同）の確率。キーは馬の並び順（0始まりの添字）を小さい順にした組。"""
    p, prob = ordered_top3_probs(strength, gamma)
    if len(prob) == 0:
        return {}
    key = np.sort(p, axis=1)
    n = len(strength)
    code = key[:, 0].astype(np.int64) * n * n + key[:, 1] * n + key[:, 2]
    uniq, inv = np.unique(code, return_inverse=True)
    sums = np.bincount(inv, weights=prob)
    return {(int(c // (n * n)), int(c // n % n), int(c % n)): float(v) for c, v in zip(uniq, sums)}


def win_probs(strength: np.ndarray) -> np.ndarray:
    s = np.asarray(strength, dtype=float)
    return s / s.sum()


@dataclass
class Calibration:
    beta: float = 1.0
    gamma: float = 1.0
    n_races: int = 0

    def strength(self, score: np.ndarray) -> np.ndarray:
        x = np.asarray(score, dtype=float)
        x = np.where(np.isfinite(x), x, np.nanmin(x[np.isfinite(x)]) if np.isfinite(x).any() else 0.0)
        z = self.beta * (x - x.max())
        return np.exp(np.clip(z, -50, 0)) + 1e-12


def _neg_loglik(params, races):
    beta, log_gamma = params
    gamma = np.exp(log_gamma)
    total = 0.0
    for x, order in races:
        z = beta * (x - x.max())
        a, b, c = order
        # 対数で計算する（強さの差が大きいと、引き算で分母が0や負になるため）
        total -= z[a] - np.logaddexp.reduce(z)
        zg = gamma * z
        rest_b = np.delete(zg, [a])
        rest_c = np.delete(zg, [a, b])
        total -= zg[b] - np.logaddexp.reduce(rest_b)
        total -= zg[c] - np.logaddexp.reduce(rest_c)
    return total


def fit_calibration(races: list[tuple[np.ndarray, tuple[int, int, int]]]) -> Calibration:
    """races: [(スコアの配列, (1着,2着,3着の添字)), ...]。同着などで並びが決まらないレースは渡さない。"""
    races = [(np.where(np.isfinite(x), x, np.nanmin(x[np.isfinite(x)]) if np.isfinite(x).any() else 0.0), o)
             for x, o in races if len(x) >= 3]
    if len(races) < 30:
        return Calibration(n_races=len(races))
    res = minimize(_neg_loglik, x0=[1.0, 0.0], args=(races,), method="L-BFGS-B",
                   bounds=[(0.01, 20.0), (np.log(0.3), np.log(1.5))])
    return Calibration(beta=float(res.x[0]), gamma=float(np.exp(res.x[1])), n_races=len(races))
