"""変異試験: 安全の仕組みを1つずつ壊した複製を mutants/ に作り、固定ケースで落ちることを確かめる（試験が効いている証拠）。"""
import json, shutil, subprocess, sys
from pathlib import Path
N = Path(__file__).resolve().parent.parent
M = [
 ("発表の鮮度を見ない", 'if pub_age > pub_max:', 'if False:'),
 ("年の補完で当年だけ", 'for y in (race.date.year - 1, race.date.year, race.date.year + 1):', 'for y in (race.date.year,):'),
 ("取り下げで完全取得を確かめない", 'elif not o["complete"]:', 'elif False:'),
 ("判断時計を確かめない", 'if not (self.judge_clock["independent"] and self.judge_clock["trusted"]):', 'if False:'),
 ("時点ビューで対象キーを見ない", 'allo = [o for o in self.obs_rows() if o.get("target") == r.key and o["kind"] == "odds"]', 'allo = [o for o in self.obs_rows() if o["kind"] == "odds"]'),
 ("予定基準の適格を正式に混ぜる", 'if v == "適格" and e["basis"] == "実際":', 'if v == "適格":'),
 ("最終オッズも中間の置き場へ", 'elif stage in (3, 4, 5):\n            stage_store = "final"', 'elif stage in (3, 4, 5):\n            stage_store = "intermediate"'),
 ("欠損率の分母に取消を含める", 'valid = [c for c in values if c not in cancel_in_odds and not set(horses_of(c)) & set(scratched)]', 'valid = list(values)'),
 ("最終確定後も発走変更を反映", 'if r.final_at is not None and ev["saved"] >= r.final_at:', 'if False:'),
 ("適格性の余白を見ない", 'elif saved >= post:', 'elif False:'),
]
out = []
for i, (name, old, new) in enumerate(M):
    d = N / "mutants" / ("m%02d" % i)
    if d.exists(): shutil.rmtree(d)
    shutil.copytree(N / "src", d / "src"); shutil.copytree(N / "tools", d / "tools", ignore=shutil.ignore_patterns("mutants.py"))
    p = d / "src/keiba_proto/system.py"; s = p.read_text()
    assert s.count(old) == 1, name
    p.write_text(s.replace(old, new))
    r = subprocess.run([sys.executable, "-I", "-B", str(d / "tools/run_cases.py"), str(N / "cases/cases.json"), str(d / "results.json")], capture_output=True, text=True)
    res = json.loads((d / "results.json").read_text())
    failed = [x["id"] for x in res["results"] if not x["ok"]]
    out.append({"mutant": name, "failed_cases": failed, "detected": bool(failed)})
    print("%-24s detected=%s %s" % (name, bool(failed), failed))
(N / "logs" / "mutants.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print("ALL_DETECTED", all(x["detected"] for x in out))
