"""三連複の配分（設計8章）。実購入はしない。方式ごとに別の仮想財布で記録する。

決まり：
  - 1レースの上限は 10,000円、100円単位
  - オッズが無いときは「未配分」。確率の裏づけ（較正済みの確率）が無いときは 0円
  - 合計が上限を超えたら、同じ比率で縮めて100円単位に切り捨てる
方式：
  equal_payout  的中したときの払戻がほぼ同じになるよう、オッズに反比例して割る
  ev_weighted   期待値（確率×オッズ）が1を超える組に、超えた分に比例して割る
  kelly         分数ケリー（既定 1/4）。組ごとに独立とみなした近似
"""
from __future__ import annotations

from dataclasses import dataclass, field

UNIT = 100
RACE_CAP = 10_000


@dataclass
class Allocation:
    status: str                      # allocated / unallocated_no_odds / zero_no_probability / zero_no_value
    stakes: dict = field(default_factory=dict)   # 組（"1-2-3"）→ 円
    method: str = ""

    @property
    def total(self) -> int:
        return sum(self.stakes.values())


def _round_down(stakes: dict, cap: int = RACE_CAP) -> dict:
    total = sum(stakes.values())
    scale = min(1.0, cap / total) if total > 0 else 0.0
    out = {k: int(v * scale // UNIT * UNIT) for k, v in stakes.items()}
    return {k: v for k, v in out.items() if v >= UNIT}


def allocate(probs: dict, odds: dict | None, method: str, budget: int = RACE_CAP,
             calibrated: bool = True, kelly_fraction: float = 0.25, max_combos: int = 10) -> Allocation:
    """probs/odds は 組 → 確率 / オッズ（倍）。"""
    if not odds:
        return Allocation("unallocated_no_odds", method=method)
    if not calibrated or not probs:
        return Allocation("zero_no_probability", method=method)
    budget = min(budget, RACE_CAP)
    cand = [(c, p, odds[c]) for c, p in probs.items() if c in odds and odds[c] > 0 and p > 0]
    cand = [x for x in cand if x[1] * x[2] > 1.0]
    cand.sort(key=lambda x: -x[1] * x[2])
    cand = cand[:max_combos]
    if not cand:
        return Allocation("zero_no_value", method=method)
    if method == "equal_payout":
        weights = {c: 1.0 / o for c, _, o in cand}
        total_w = sum(weights.values())
        raw = {c: budget * w / total_w for c, w in weights.items()}
    elif method == "ev_weighted":
        edge = {c: p * o - 1.0 for c, p, o in cand}
        total_e = sum(edge.values())
        raw = {c: budget * e / total_e for c, e in edge.items()}
    elif method == "kelly":
        raw = {}
        for c, p, o in cand:
            b = o - 1.0
            f = (p * b - (1 - p)) / b
            if f > 0:
                raw[c] = budget * kelly_fraction * f
    else:
        raise ValueError(f"unknown method: {method}")
    stakes = _round_down(raw, cap=budget)
    return Allocation("allocated" if stakes else "zero_no_value", stakes, method)
