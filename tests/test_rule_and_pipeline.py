import numpy as np
import pandas as pd

from keiba.evaluate import EvalConfig, actual_order, run_evaluation
from keiba.features.asof import build_features
from keiba.ledger import Ledger
from keiba.models.rule import GROUPS, score_groups


def test_rule_points_and_unscored():
    df = pd.DataFrame({"race_key": ["r"] * 4, "best_fig5": [3.0, 2.0, 1.0, np.nan], "mean_fig3": [np.nan] * 4})
    out = score_groups(df, "1h")
    assert out.loc[0, "ability"] == 10 and out.loc[2, "ability"] < out.loc[1, "ability"]
    assert pd.isna(out.loc[3, "ability"])  # 材料なし → 未採点
    assert out["rule_coverage"].max() <= 1 / len(GROUPS) + 1e-9


def test_going_unscored_at_24h():
    df = pd.DataFrame({"race_key": ["r"] * 3, "going_apt": [0.1, 0.5, 0.9]})
    assert score_groups(df, "24h")["going"].isna().all()
    assert score_groups(df, "1h")["going"].notna().all()


def test_actual_order_dead_heat_is_none():
    g = pd.DataFrame({"status": ["finished"] * 4, "finish_position": [1, 2, 2, 4], "horse_number": [1, 2, 3, 4]})
    assert actual_order(g) is None


def test_pipeline_end_to_end_on_synthetic(tmp_path):
    from tests.conftest import make_tables
    races, runners = make_tables(n_days=30, races_per_day=6, field=10, horses=60)
    feat = build_features(races, runners)
    pay = pd.DataFrame([{"race_key": rk, "bet_type": "trio",
                         "combo": "-".join(map(str, sorted(g.nsmallest(3, "finish_position")["horse_number"]))),
                         "payout_yen": 1500, "source": "test", "check": "test"}
                        for rk, g in runners.groupby("race_key")])
    led = Ledger(tmp_path / "l.sqlite")
    cfg = EvalConfig(first_month="2025-05-01", holdout_start="2025-07-01", methods=("rule", "base_logit", "base_pop"))
    result, rows = run_evaluation(feat, races, runners, pay, led, cfg, log=lambda *_: None)
    assert {s["period"] for s in result["summary"]} == {"walk_forward", "holdout"}
    assert led.verify()["hash_mismatch"] == 0
    # 同じ版で入れ直しても重複しない（同じ中身は何もしない）
    n = led.verify()["predictions"]
    run_evaluation(feat, races, runners, pay, led, cfg, log=lambda *_: None)
    assert led.verify()["predictions"] == n
    p = next(led.predictions())
    assert p["allocation"]["status"] == "unallocated_no_odds" and len(p["trios"]) == 20
