"""時点の漏れ防止（設計11章の抜き打ち・おとり試験）。"""
import numpy as np
import pandas as pd

from keiba.features.asof import FEATURES_24H, FEATURES_1H_EXTRA, build_features, feature_columns
from keiba.schema import POST_RACE_ONLY, available

COLS = FEATURES_24H + FEATURES_1H_EXTRA


def _same(a: pd.DataFrame, b: pd.DataFrame):
    a = a.set_index("runner_key")[COLS].sort_index()
    b = b.set_index("runner_key").loc[a.index, COLS]
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_future_decoy_rows_do_not_change_past_features(tables):
    races, runners = tables
    base = build_features(races, runners)
    # おとり：最後の日の結果を極端な値に書き換える（未来の情報）
    last = runners["race_date"].max()
    decoy = runners.copy()
    m = decoy["race_date"] == last
    decoy.loc[m, "finish_position"] = decoy.loc[m, "finish_position"].max() + 1 - decoy.loc[m, "finish_position"]
    decoy.loc[m, "finish_time_s"] = 1.0
    decoy.loc[m, "last3f_s"] = 99.0
    after = build_features(races, decoy)
    # 最後の日の特徴も、それ以前の日の特徴も変わらない（その日の結果は、その日の特徴に入らない）
    _same(base, after)


def test_same_day_other_race_results_are_not_used(tables):
    races, runners = tables
    base = build_features(races, runners)
    day = sorted(runners["race_date"].unique())[3]
    first_race = runners.loc[runners["race_date"] == day, "race_key"].iloc[0]
    changed = runners.copy()
    m = changed["race_key"] == first_race
    changed.loc[m, "finish_position"] = changed.loc[m, "finish_position"][::-1].to_numpy()
    after = build_features(races, changed)
    same_day = base["race_date"] == pd.Timestamp(day)
    _same(base[same_day], after[after["race_date"] == pd.Timestamp(day)])


def test_spot_check_truncated_rebuild_matches(tables):
    """ランダムな日付で切ったデータだけで作り直しても、その日の特徴は同じ。"""
    races, runners = tables
    full = build_features(races, runners)
    rng = np.random.default_rng(3)
    for day in rng.choice(sorted(runners["race_date"].unique()), size=3, replace=False):
        cut = runners[runners["race_date"] <= day]
        part = build_features(races[races["race_date"] <= day], cut)
        _same(full[full["race_date"] == pd.Timestamp(day)], part[part["race_date"] == pd.Timestamp(day)])


def test_post_race_columns_never_features():
    for tp in ("24h", "1h", "15m"):
        cols = set(feature_columns(tp))
        assert not cols & POST_RACE_ONLY
        assert "final_win_odds" not in cols and "final_popularity" not in cols


def test_time_point_tiers():
    assert available("weight_carried", "24h")
    assert not available("body_weight", "24h") and available("body_weight", "1h")
    assert "body_weight" not in feature_columns("24h") and "body_weight" in feature_columns("1h")
    assert not available("final_win_odds", "15m")


def test_withdrawn_horses_are_not_predicted(tables):
    races, runners = tables
    runners = runners.copy()
    runners.loc[0, "status"] = "withdrawn"
    feat = build_features(races, runners)
    assert runners.loc[0, "runner_key"] not in set(feat["runner_key"])
