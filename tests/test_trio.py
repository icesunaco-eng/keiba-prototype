from itertools import permutations

import numpy as np

from keiba.models.trio import Calibration, fit_calibration, ordered_top3_probs, trio_probs


def test_trio_probabilities_sum_to_one():
    s = np.array([5.0, 3.0, 2.0, 1.0, 0.5, 0.2])
    for gamma in (0.7, 1.0, 1.2):
        assert abs(sum(trio_probs(s, gamma).values()) - 1) < 1e-9


def test_matches_brute_force_plackett_luce():
    s = np.array([4.0, 2.0, 1.0, 1.0, 0.5])
    expected = {}
    for a, b, c in permutations(range(5), 3):
        p = s[a] / s.sum() * s[b] / (s.sum() - s[a]) * s[c] / (s.sum() - s[a] - s[b])
        key = tuple(sorted((a, b, c)))
        expected[key] = expected.get(key, 0) + p
    got = trio_probs(s, 1.0)
    for k, v in expected.items():
        assert abs(got[k] - v) < 1e-12


def test_strongest_trio_ranks_first():
    s = np.array([0.1, 9.0, 0.2, 8.0, 7.0, 0.3])
    probs = trio_probs(s)
    assert max(probs, key=probs.get) == (1, 3, 4)


def test_fit_calibration_recovers_beta():
    rng = np.random.default_rng(1)
    true_beta = 1.5
    races = []
    for _ in range(400):
        x = rng.normal(size=10)
        s = np.exp(true_beta * x)
        order, p = ordered_top3_probs(s)
        races.append((x, tuple(int(v) for v in order[rng.choice(len(p), p=p / p.sum())])))
    cal = fit_calibration(races)
    assert abs(cal.beta - true_beta) < 0.35
    assert 0.7 < cal.gamma < 1.3


def test_missing_scores_get_weakest_strength():
    s = Calibration(beta=1.0).strength(np.array([1.0, np.nan, 0.0]))
    assert s[1] <= s[2] < s[0]
