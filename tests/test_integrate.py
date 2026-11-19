from keiba.integrate import pool

A = {"1-2-3": 0.5, "1-2-4": 0.3, "1-3-4": 0.2}
B = {"1-2-3": 0.2, "1-2-4": 0.6, "2-3-4": 0.2}


def test_single_method_weight_one_is_identity():
    out = pool({"a": A, "b": B}, {"a": 1.0, "b": 0.0})
    assert out == A


def test_agreement_is_rewarded_and_sums_to_one():
    out = pool({"a": A, "b": B}, {"a": 1.0, "b": 1.0})
    assert abs(sum(out.values()) - 1) < 1e-12
    assert max(out, key=out.get) == "1-2-4"  # 0.3×0.6 が一番大きい
    assert "2-3-4" in out and "1-3-4" in out  # 片方にしか無い組も残る（無い側は最小の半分）


def test_no_weights_gives_empty():
    assert pool({"a": A}, {"a": 0}) == {}
