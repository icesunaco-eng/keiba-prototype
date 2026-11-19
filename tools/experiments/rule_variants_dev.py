"""ルールの比較版を1つずつ比べる（開発用の参考）。期間は調整期間 2025-05〜2026-05 だけ。
2026-06 以降は結果を見たので使わない。正式な比較は、これからのレースで行う（あいすとの取り決め）。"""
import json
import numpy as np, pandas as pd
from pathlib import Path
import keiba.evaluate as ev
from keiba.ledger import Ledger
from keiba.models import rule_v1

VERS = ["v1.0"] + rule_v1.COMPARISON_VARIANTS
names = {v: "rv_" + v.replace(".", "").replace("-", "_") for v in VERS}
orig = ev.make_model
ev.make_model = lambda m, tp: rule_v1.RuleV1Model(tp, next(v for v, n in names.items() if n == m)) if m in names.values() else orig(m, tp)
for n in names.values():
    ev.METHOD_KIND[n] = "rule"
_pred = ev.predict_race
ev.predict_race = lambda g, sc, cal, m, tp, ver, rg: _pred(g, sc, cal, "rule_v01" if m in names.values() else m, tp, ver, None)

t = Path("data/tables")
races, runners, pay = (pd.read_parquet(t / f) for f in ("races.parquet", "runners.parquet", "payouts.parquet"))
feat = pd.read_parquet(t / "features.parquet")
cfg = ev.EvalConfig(methods=tuple(names.values()), time_points=("1h",), run_holdout=False, last_date="2026-05-31")
res, rows = ev.run_evaluation(feat, races, runners, pay, Ledger(Path("data/experiments/ledger_rule_variants.sqlite")), cfg, log=lambda *a: None)
f = feat[(feat.race_date >= "2025-05-01") & (feat.race_date <= "2026-05-31")]
out = []
for v, n in names.items():
    s = next(x for x in res["summary"] if x["method"] == n)
    sf = rule_v1.score_frame(f, "1h", rule_v1.VARIANTS[v]["prefix"])
    out.append({"版": v, "総合点が付く馬": round(sf["rule_total"].notna().mean(), 3), "R": s["races"],
                "上位10組": s["hit@10"], "上位20組": s["hit@20"], "対数損失": s["trio_logloss"]})
df = pd.DataFrame(out)
print(df.to_string(index=False))
df.to_csv("data/experiments/rule_variants_dev.csv", index=False)
