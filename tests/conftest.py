"""小さな合成データ（実データはコミットしない）。"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def before_post_clock(monkeypatch):
    """合成のレース（2025-08-30 15:40発走）の1時間前の締切の後・発走前として予想を作る。発走後の試験は now を渡す。"""
    from datetime import datetime, timedelta, timezone

    from keiba.live import wallet
    at = datetime(2025, 8, 30, 14, 41, tzinfo=timezone(timedelta(hours=9)))
    monkeypatch.setattr(wallet, "prediction_clock", lambda: at)
    from keiba.ledger import Ledger
    monkeypatch.setattr(Ledger, "_now", lambda self: at.astimezone(timezone.utc).isoformat(timespec="seconds"))

VENUES = ["東京", "京都"]


def make_tables(n_days=8, races_per_day=4, field=10, seed=0, horses=40):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-04", periods=n_days, freq="7D")
    race_rows, runner_rows = [], []
    ability = rng.normal(size=horses)
    for d in dates:
        for r in range(1, races_per_day + 1):
            venue = VENUES[r % 2]
            rk = f"{d.date()}|{venue}|{r}"
            surface = "芝" if r % 2 else "ダート"
            dist = 1600 if r % 2 else 1200
            race_rows.append({"race_key": rk, "race_date": str(d.date()), "venue": venue, "race_number": r,
                              "provider_race_id": None, "owner": "test", "surface": surface, "distance_m": dist,
                              "turn": "右", "going": "良", "weather": "晴", "race_class": "未勝利",
                              "field_size": field, "post_time": "12:00", "race_last3f_s": 35.0,
                              "race_last4f_s": 47.0, "attr_source": "test", "race_type": "平地候補"})
            ids = rng.choice(horses, size=field, replace=False)
            perf = ability[ids] + rng.normal(scale=0.8, size=field)
            order = np.argsort(-perf)
            for pos, idx in enumerate(order, start=1):
                h = int(ids[idx])
                hn = int(idx) + 1
                runner_rows.append({
                    "runner_key": f"{rk}|{hn}", "race_key": rk, "race_date": str(d.date()), "horse_number": hn,
                    "horse_id": f"H{h:04d}", "horse_name": f"馬{h}", "status": "finished", "counts_as_starter": True,
                    "finish_position": pos, "dead_heat": False, "finish_time_s": 95 + pos * 0.2 + (dist - 1200) / 20,
                    "waku": (hn + 1) // 2, "sex": "牡", "age": 3, "weight_carried": 56.0,
                    "jockey_key": f"nk:J{h % 7}", "trainer_key": f"nk:T{h % 5}", "body_weight": 470,
                    "body_weight_diff": 0, "final_win_odds": float(2 + pos), "final_popularity": pos,
                    "last3f_s": 34 + pos * 0.1, "passage_raw": f"{pos}-{pos}", "margin_raw": None,
                    "attr_source": "test", "venue": venue, "race_number": r, "owner": "test",
                })
    return pd.DataFrame(race_rows), pd.DataFrame(runner_rows)


@pytest.fixture
def tables():
    return make_tables()
