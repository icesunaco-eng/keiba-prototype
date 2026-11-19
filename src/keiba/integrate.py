"""あいす統合（方式をまとめる最終判断）の計算の部品。まだ財布にはつないでいない（重みが決まっていないため）。

まとめ方：対数線形の足し合わせ（log-linear pooling）
  p_統合(組) ∝ Π_方式 p_方式(組)^w_方式
  - 重みが全部1なら「全方式が同意する組」が強くなる。市場だけ重み1なら市場と同じ
  - ある方式が組に確率を付けていない（上位20組に入っていない）ときは、その方式の最小確率の半分とみなす
重みは、これからのレース（まっさらなデータ）の成績から決める。決め方はあいすと相談（docs/requests/aisu-integration-proposal.md）。
"""
from __future__ import annotations

import math


def pool(method_probs: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, float]:
    """method_probs：方式 → {組: 確率}。weights：方式 → 重み（0 なら使わない）。返り値は合計1の {組: 確率}。"""
    used = {m: p for m, p in method_probs.items() if weights.get(m, 0) > 0 and p}
    if not used:
        return {}
    combos = set().union(*used.values())
    floor = {m: min(v for v in p.values() if v > 0) / 2 for m, p in used.items()}
    logp = {c: sum(weights[m] * math.log(p.get(c) or floor[m]) for m, p in used.items()) for c in combos}
    top = max(logp.values())
    raw = {c: math.exp(v - top) for c, v in logp.items()}
    total = sum(raw.values())
    return {c: v / total for c, v in sorted(raw.items(), key=lambda x: -x[1])}
