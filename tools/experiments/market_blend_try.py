"""学習モデルが市場（単勝オッズ）に無い情報を持っているかの実験。封印期間（2026-08-16〜）は使わない。"""
import numpy as np, pandas as pd
from pathlib import Path
import keiba.evaluate as ev
from keiba.ledger import Ledger
t = Path("data/tables")
races, runners, pay = (pd.read_parquet(t / f) for f in ("races.parquet", "runners.parquet", "payouts.parquet"))
feat = pd.read_parquet(t / "features.parquet")
cfg = ev.EvalConfig(methods=("ml_market", "base_pop"), time_points=("1h",), run_holdout=False, last_date="2026-08-15")
res, rows = ev.run_evaluation(feat, races, runners, pay, Ledger(Path("data/experiments/ledger_market.sqlite")), cfg, log=lambda *a: None)
for s in res["summary"]:
    print(s["method"], s["races"], s["trio_logloss"], s["hit@5"], s["hit@10"], s["ref_top10_equal"]["roi"])
v = rows[rows.method == "ml_market"]
print({k: x for k, x in res["importance"].items() if "market_v" in k})
