import math

import pytest

from keiba.integrate import pool

A = {"1-2-3": 0.5, "1-2-4": 0.3, "1-3-4": 0.2}
B = {"1-2-3": 0.2, "1-2-4": 0.6, "2-3-4": 0.2}


def test_single_method_weight_one_is_identity():
    out = pool({"a": A, "b": B}, {"a": 1.0, "b": 0.0})
    assert out.keys() == A.keys() and all(abs(out[c] - A[c]) < 1e-12 for c in A)


def test_agreement_is_rewarded_and_sums_to_one():
    out = pool({"a": A, "b": B}, {"a": 1.0, "b": 1.0})
    assert abs(sum(out.values()) - 1) < 1e-12
    assert max(out, key=out.get) == "1-2-4"  # 0.3×0.6 が一番大きい
    assert "2-3-4" in out and "1-3-4" in out  # 片方にしか無い組も残る（無い側は最小の半分）


def test_no_weights_gives_empty():
    assert pool({"a": A}, {"a": 0}) == {}


def test_tie_order_and_values_do_not_depend_on_process():
    """同率の組の順位・保存する値が、起動ごとの集合の並びで変わらない。"""
    import os
    import subprocess
    import sys
    code = ("from keiba.integrate import pool; from itertools import combinations as c; "
            "a = {'-'.join(map(str, x)): 1 / 56 for x in c(range(1, 9), 3)}; "
            "b = {k: (0.3 if k == '1-2-3' else 0.7 / 55) for k in a}; "
            "print(list(pool({'m': a}, {'m': 1.0}).items())[:20], list(pool({'m': a, 'n': b}, {'m': 1.0, 'n': 0.4}).items()))")
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": os.pathsep.join(sys.path)}).stdout
            for seed in range(6)}
    assert len(runs) == 1


def test_ties_are_ordered_by_combo():
    out = pool({"m": {"2-3-4": 0.25, "1-2-3": 0.25, "1-2-4": 0.5}}, {"m": 1.0})
    assert list(out) == ["1-2-4", "1-2-3", "2-3-4"]


@pytest.mark.parametrize("tiny", [math.ulp(0.0), 2 * math.ulp(0.0), 1e-320, 1e-300])
def test_smallest_probability_does_not_stop_pooling(tiny):
    """極小の確率の半分が0に潰れても止まらない。付いていない組は最小値より大きくならない。"""
    out = pool({"a": {"1-2-3": tiny, "1-2-5": 1.0}, "b": {"1-2-4": 0.5, "1-2-5": 0.5}}, {"a": 0.2, "b": 1.0})
    assert len(out) == 3 and all(math.isfinite(v) and v > 0 for v in out.values())
    assert abs(sum(out.values()) - 1) < 1e-12 and out["1-2-4"] <= out["1-2-5"]


def test_floor_is_still_half_of_the_minimum():
    out = pool({"a": {"1-2-3": 0.2, "1-2-4": 0.8}, "b": {"1-2-5": 1.0}}, {"a": 1.0, "b": 1.0})
    # 1-2-5 は a に付いていない → a の最小 0.2 の半分 0.1。1-2-3 は b に付いていない → b の最小 1.0 の半分
    assert out["1-2-5"] / out["1-2-3"] == pytest.approx((0.1 * 1.0) / (0.2 * 0.5))
