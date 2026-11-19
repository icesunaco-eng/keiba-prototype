"""正規の表と、各列の「使える時点」。

予想の時点は 24h（前日）・1h（発走1時間前）・15m（発走15分前）。
列ごとに一番早く使える時点を持たせ、特徴の選択はここから自動で決める。
確定オッズ・確定人気は発走後の値なので、特徴には使わない（比較用の基準モデルだけが使う）。
"""
from __future__ import annotations

TIME_POINTS = ("24h", "1h", "15m")
_ORDER = {t: i for i, t in enumerate(TIME_POINTS)}

# 発走前に分かる列：どの時点から使えるか
PRE_RACE_AVAILABLE_FROM = {
    # 出馬表（前々日〜前日に確定）
    "horse_number": "24h", "waku": "24h", "sex": "24h", "age": "24h",
    "weight_carried": "24h", "jockey_key": "24h", "trainer_key": "24h",
    "distance_m": "24h", "surface": "24h", "venue": "24h", "turn": "24h",
    "race_class": "24h", "field_size": "24h", "race_number": "24h",
    # 当日に発表
    "going": "1h", "weather": "1h", "body_weight": "1h", "body_weight_diff": "1h",
}

# 発走後にしか分からない列（そのレースの特徴に入れてはいけない）
POST_RACE_ONLY = {
    "finish_position", "status", "finish_time_s", "margin_raw", "last3f_s",
    "passage_raw", "final_win_odds", "final_popularity", "trio_payout",
}

RACES_COLUMNS = [
    "race_key", "race_date", "venue", "race_number", "provider_race_id", "owner",
    "surface", "distance_m", "turn", "going", "weather", "race_class", "field_size",
    "post_time", "race_last3f_s", "race_last4f_s", "attr_source",
]
RUNNERS_COLUMNS = [
    "runner_key", "race_key", "race_date", "horse_number", "horse_id", "horse_name",
    "status", "counts_as_starter", "finish_position", "dead_heat", "finish_time_s",
    "waku", "sex", "age", "weight_carried", "jockey_key", "trainer_key",
    "body_weight", "body_weight_diff", "final_win_odds", "final_popularity",
    "last3f_s", "passage_raw", "margin_raw", "attr_source",
]
PAYOUTS_COLUMNS = ["race_key", "bet_type", "combo", "payout_yen", "source", "check"]


def available(column: str, time_point: str) -> bool:
    """その列が、その時点の予想で使えるか。"""
    if column in POST_RACE_ONLY:
        return False
    src = PRE_RACE_AVAILABLE_FROM.get(column)
    if src is None:
        return False
    return _ORDER[src] <= _ORDER[time_point]
