"""rule-v1.1 案の実験。封印期間（2026-08-16〜）は使わない。調整期間 2025-05〜2026-05、確認期間 2026-06〜08-15。"""
import json, sys
import numpy as np, pandas as pd
from pathlib import Path
import keiba.evaluate as ev
from keiba.models import rule_v1
from keiba.ledger import Ledger

class Variant(rule_v1.RuleV1Model):
    def __init__(self, tp, variant, neutral):
        super().__init__(tp, variant); self.neutral = neutral
    def score(self, df):
        t = pd.to_numeric(rule_v1.score_frame(df, self.time_point, self.prefix)["rule_total"], errors="coerce")
        if self.neutral:
            t = t.fillna(t.groupby(df["race_key"]).transform("mean"))
        return t.to_numpy(dtype=float)
    def fingerprint(self):
        return f"exp:{self.variant}:{self.neutral}"

MODELS = {"r10_spec": ("v1.0", False), "r10_neutral": ("v1.0", True), "r11_weakest": ("v1.1", False), "r11_full": ("v1.1", True)}
orig = ev.make_model
ev.make_model = lambda m, tp: Variant(tp, *MODELS[m]) if m in MODELS else orig(m, tp)
for m in MODELS: ev.METHOD_KIND[m] = "rule"
_orig_pred = ev.predict_race
def pred(g, sc, cal, method, tp, ver, rg):
    return _orig_pred(g, sc, cal, "rule_v01" if method in MODELS else method, tp, ver, rg)
ev.predict_race = pred

t = Path("data/tables")
races, runners, pay = (pd.read_parquet(t / f) for f in ("races.parquet", "runners.parquet", "payouts.parquet"))
feat = pd.read_parquet(t / "features.parquet")
cfg = ev.EvalConfig(methods=("rule_v01",) + tuple(MODELS), time_points=("1h",), run_holdout=False, last_date="2026-08-15")
led = Ledger(Path("data/experiments/ledger_rule_v11.sqlite"))
res, rows = ev.run_evaluation(feat, races, runners, pay, led, cfg, log=lambda *a: None)
rows["phase"] = np.where(rows["race_date"] <= "2026-05-31", "調整 2025-05〜2026-05", "確認 2026-06〜08-15")
out = []
for (ph, m), g in rows.groupby(["phase", "method"]):
    v = g[g["actual"].notna()]
    s = g[g["ref_top10_equal_status"] == "settled"]
    out.append({"期間": ph, "方式": m, "R": len(g), "対数損失": round(-np.log(v["p_actual"].clip(lower=1e-6)).mean(), 4),
                "上位10": round(((v.rank_actual <= 10) & v.rank_actual.notna()).mean(), 4),
                "上位20": round(((v.rank_actual <= 20) & v.rank_actual.notna()).mean(), 4),
                "参考回収率10点": round(s["ref_top10_equal_return"].sum() / s["ref_top10_equal_stake"].sum(), 4),
                "Zレース率": round((g["confidence"] == "Z").mean(), 3)})
df = pd.DataFrame(out)
# 総合点が付く馬の割合
f = feat[(feat.race_date >= "2025-05-01") & (feat.race_date <= "2026-08-15")]
cov = {}
for v, pre in (("v1.0", "u_"), ("v1.1", "u11_")):
    sf = rule_v1.score_frame(f, "1h", pre)
    cov[v] = {"総合点が付く馬": round(sf["rule_total"].notna().mean(), 3),
              **{f"群:{g}": round(pd.to_numeric(sf[g], errors="coerce").notna().mean(), 3) for g in rule_v1.GROUPS}}
print(df.to_string(index=False)); print(json.dumps(cov, ensure_ascii=False, indent=1))
df.to_csv("data/experiments/rule_v11_result.csv", index=False); json.dump(cov, open("data/experiments/rule_v11_coverage.json", "w"), ensure_ascii=False, indent=1)
