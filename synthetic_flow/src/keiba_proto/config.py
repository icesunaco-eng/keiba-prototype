"""閾値はすべてここ1か所（v0.3 6章・2章・7章の設計案の値）。境界の含み方は「以内・以下は含む／超は含まない」。"""
from datetime import timedelta

# 鮮度（秒）: 時点 -> (取得の鮮度の上限, 発表の鮮度の上限)
FRESHNESS = {"15m": (5 * 60, 10 * 60), "1h": (15 * 60, 30 * 60), "24h": (60 * 60, 120 * 60)}
TIMEPOINT_OFFSET = {"24h": timedelta(hours=24), "1h": timedelta(hours=1), "15m": timedelta(minutes=15)}
FINAL_TIMEPOINT = "15m"

SKEW_NORMAL_MAX = 2.0     # 秒。これ以下は正常
SKEW_HALT_OVER = 30.0     # 秒。これを超えたら取得停止・隔離（これ以下は警告＋安全側）

MISSING_LOW_PCT = 5       # 0%超〜5%以下: 欠けた組だけ未配分
MISSING_HIGH_PCT = 20     # 5%超〜20%以下: 自信度の上限C / 20%超: 未配分（欠損）・自信度Z

ELIGIBILITY_MARGIN_SEC = 60   # 適格: 確定保存が判定用発走時刻の60秒以上前
MINUTE_PAD_SEC = 60           # 分精度の発表時刻は分の終わりを利用可能時刻にする
YEAR_WINDOW_DAYS = 3          # 年の補完: 開催日の3日前0:00〜3日後24:00

SALE_START_DAY_BEFORE = (19, 0)   # 架空の発売開始予定: 前日19:00
SALE_GRACE_MIN = 30               # これを超えて未発表なら取得失敗（異常）

# 固定ダミー買い目（予想モデルは対象外）
AXIS = 1
BETS = ["1-2-3", "1-2-4", "1-3-4"]
REBUILD_AXIS = 2
REBUILD_BETS = ["2-3-4", "2-3-5", "2-4-5"]
AMOUNT = 1000
MARKS = {"◎": 1, "○": 2, "⭐": 3}
FIELD_SIZE = 16
METHOD = "dummy-fixed"
VERSION = "proto-0.1"
