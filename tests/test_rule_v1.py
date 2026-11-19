"""rule-v1.0 の受け入れテスト（あいすの rule-notes.md「実装受け入れテスト」の表のとおり）。"""
import numpy as np
import pandas as pd

from keiba.models import rule_v1
from keiba.models.rule_v1 import GROUPS, build_utilities, coverage_label, quantize, score_race, total_and_coverage


def test_three_distinct_utilities():
    pts, tied = quantize([0.9, 0.5, 0.1])
    assert pts == [10, 6, 1] and not tied
    assert quantize([0.1, 0.9, 0.5])[0] == [1, 10, 6]  # 入力順では不変


def test_two_tied_for_first():
    assert quantize([0.9, 0.9, 0.1])[0] == [8, 8, 1]


def test_eleven_runners_second_and_last():
    pts, _ = quantize([1.0 - i * 0.05 for i in range(11)])
    assert pts[1] == 10 and pts[-1] == 1


def test_valid_fraction_rules():
    assert quantize([0.5, None])[0] == [None, None]                     # 有効1頭
    assert quantize([0.5, 0.4] + [None] * 3)[0] == [None] * 5          # 有効40%（49%未満）
    assert quantize([0.5, 0.4, None, None])[0][:2] == [10, 1]          # 50%かつ2頭なら計算


def test_all_tied_shows_6_but_not_counted():
    pts, tied = quantize([0.3, 0.3, 0.3])
    assert pts == [6, 6, 6] and tied
    points = {g: 6 for g in GROUPS}
    t, c = total_and_coverage(points, {g: True for g in GROUPS})
    assert t is None and c == 0


def test_coverage_levels():
    expect = {3: (0.375, "Z"), 4: (0.5, "D"), 5: (0.625, "C"), 6: (0.75, "B"), 7: (0.875, "A")}
    for k, (cov, label) in expect.items():
        points = {g: (5 if i < k else None) for i, g in enumerate(GROUPS)}
        t, c = total_and_coverage(points, {})
        assert c == cov and coverage_label(c, t) == label
        assert (t is None) == (label == "Z")


def test_total_of_four_groups():
    points = dict(zip(GROUPS, [4, 6, 8, 10, None, None, None, None]))
    t, c = total_and_coverage(points, {})
    assert round(t, 2) == 7.00 and c == 0.5 and coverage_label(c, t) == "D"


def test_jockey_shrinkage_formula():
    assert abs((5 + 30 * 0.20) / (20 + 30) - 0.22) < 1e-12


# ---- 合成データでの as-of の性質 ----

def _tables(n_days=40, field=9, seed=2):
    rng = np.random.default_rng(seed)
    horses = 30
    ab = rng.normal(size=horses)
    races, runners = [], []
    for di, d in enumerate(pd.date_range("2025-01-04", periods=n_days, freq="3D")):
        for r in (1, 2):
            rk = f"{d.date()}|東京|{r}"
            races.append({"race_key": rk, "surface": "芝", "distance_m": 1600, "going": "良", "venue": "東京", "race_type": "平地候補"})
            ids = rng.choice(horses, size=field, replace=False)
            perf = ab[ids] + rng.normal(scale=0.5, size=field)
            for pos, idx in enumerate(np.argsort(-perf), start=1):
                hn = int(idx) + 1
                runners.append({"runner_key": f"{rk}|{hn}", "race_key": rk, "race_date": str(d.date()), "horse_number": hn,
                                "horse_id": f"H{ids[idx]}", "status": "finished", "finish_position": pos,
                                "finish_time_s": 95 + pos * 0.2, "waku": (hn + 1) // 2, "weight_carried": 56.0,
                                "jockey_key": f"nk:J{ids[idx] % 4}", "last3f_s": 34 + pos * 0.1, "passage_raw": f"{pos}-{pos}"})
    return pd.DataFrame(races), pd.DataFrame(runners)


def test_historic_finish_fraction():
    races, runners = _tables()
    u = build_utilities(races, runners)
    assert u["u_recent"].notna().any()
    assert u["u_recent"].dropna().between(0, 1).all()


def test_current_race_results_do_not_change_scores():
    races, runners = _tables()
    base = build_utilities(races, runners).set_index("runner_key")
    rk = runners["race_key"].iloc[-1]
    changed = runners.copy()
    m = changed["race_key"] == rk
    changed.loc[m, "finish_position"] = changed.loc[m, "finish_position"][::-1].to_numpy()
    changed.loc[m, "last3f_s"] = 30.0
    after = build_utilities(races, changed).set_index("runner_key")
    keys = runners.loc[m, "runner_key"]
    pd.testing.assert_frame_equal(base.loc[keys], after.loc[keys])


def test_same_day_other_race_does_not_change_scores():
    races, runners = _tables()
    base = build_utilities(races, runners).set_index("runner_key")
    day = runners["race_date"].iloc[-1]
    first = runners.loc[runners["race_date"] == day, "race_key"].iloc[0]
    second = runners.loc[(runners["race_date"] == day) & (runners["race_key"] != first), "runner_key"]
    changed = runners.copy()
    m = changed["race_key"] == first
    changed.loc[m, "finish_position"] = changed.loc[m, "finish_position"][::-1].to_numpy()
    after = build_utilities(races, changed).set_index("runner_key")
    pd.testing.assert_frame_equal(base.loc[second], after.loc[second])


def test_going_unscored_the_day_before():
    races, runners = _tables()
    u = build_utilities(races, runners)
    last = u[u["race_key"] == u["race_key"].iloc[-1]].copy()
    last["u_going"] = np.linspace(0.1, 0.9, len(last))
    assert score_race(last, "24h")["going"].isna().all()
    assert score_race(last, "1h")["going"].notna().all()


def test_jump_races_are_out_of_scope():
    races, runners = _tables()
    races.loc[races.index[-1], "surface"] = "障害"
    u = build_utilities(races, runners)
    last = u[u["race_key"] == races["race_key"].iloc[-1]]
    s = score_race(last, "1h")
    assert s["rule_total"].isna().all() and (s["confidence"] == "Z").all()


def test_mode_is_labelled_offline_proxy():
    assert rule_v1.MODE == "offline_proxy"
