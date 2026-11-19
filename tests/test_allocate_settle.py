from keiba.allocate import RACE_CAP, UNIT, allocate
from keiba.settle import combo_key, settle_trio

PROBS = {"1-2-3": 0.10, "1-2-4": 0.08, "1-3-4": 0.05, "2-3-4": 0.02}
ODDS = {"1-2-3": 15.0, "1-2-4": 20.0, "1-3-4": 30.0, "2-3-4": 40.0}


def test_no_odds_is_unallocated():
    assert allocate(PROBS, None, "kelly").status == "unallocated_no_odds"


def test_uncalibrated_is_zero():
    a = allocate(PROBS, ODDS, "ev_weighted", calibrated=False)
    assert a.status == "zero_no_probability" and a.total == 0


def test_units_and_cap():
    for method in ("equal_payout", "ev_weighted", "kelly"):
        a = allocate(PROBS, ODDS, method, budget=50_000)
        assert a.total <= RACE_CAP
        assert all(v % UNIT == 0 and v >= UNIT for v in a.stakes.values())
        assert "2-3-4" not in a.stakes  # 期待値 0.8 < 1 は買わない


def test_equal_payout_returns_similar_amounts():
    a = allocate(PROBS, ODDS, "equal_payout")
    payouts = [a.stakes[c] * ODDS[c] for c in a.stakes]
    assert max(payouts) - min(payouts) <= max(ODDS.values()) * UNIT


def test_no_value_is_zero():
    a = allocate(PROBS, {k: 1.5 for k in ODDS}, "ev_weighted")
    assert a.status == "zero_no_value" and a.total == 0


def test_settle_hit_and_miss():
    st = settle_trio({"1-2-3": 100, "4-5-6": 100}, 10, set(), {"1-2-3": 2350})
    assert st.status == "settled" and st.returned == 2350 and st.hits == ["1-2-3"]


def test_settle_dead_heat_pays_any_winning_combo():
    st = settle_trio({"1-2-4": 200}, 10, set(), {"1-2-3": 1000, "1-2-4": 1500})
    assert st.returned == 3000


def test_settle_scratched_horse_refunds_that_combo():
    st = settle_trio({"1-2-9": 100, "1-2-3": 100}, 10, {9}, {"1-2-3": 800})
    assert st.refunds == ["1-2-9"] and st.returned == 100 + 800


def test_settle_void_race_refunds_all():
    st = settle_trio({"1-2-3": 300}, 2, set(), None)
    assert st.status == "refunded_void" and st.returned == 300


def test_settle_without_payout_is_unsettled():
    assert settle_trio({"1-2-3": 100}, 10, set(), None).status == "unsettled_no_payout"


def test_combo_key_sorts():
    assert combo_key([10, 2, 7]) == "2-7-10"
