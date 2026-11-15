"""のあの新規試験（🤍20261115-7、KD-01〜05・SD-01）。架空データのみ・通信なし。
みにの別入力（2096〜2097年、Q11、24.7）とは別の入力にした：2098年3月、レースR05/R06、オッズ18.3。
期待値は設計（v0.2・v0.3）とみにの指摘から先に書いた。実装の出力から逆算していない。
各ケースは「対象」（修正前に失敗するはず）と「対照」（修正前から成功するはず）に分ける。
"""
from pathlib import Path
from datetime import datetime, timedelta
from itertools import combinations
import sys, json, copy, traceback

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
from keiba_proto.system import System  # noqa: E402

D1 = datetime(2098, 3, 14)
D2 = datetime(2098, 3, 15)
COMBOS = ["-".join(map(str, c)) for c in combinations(range(1, 17), 3)]
BETS = {"1-2-3", "1-2-4", "1-3-4"}
RESULTS = []


def fresh(rid="R05", day=D1):
    s = System()
    s.add_race(rid, day, day.replace(hour=15, minute=40))
    return s


def odds(s, rid, ended, announce, values=None, skew=0, obs_id=None, stage=1):
    r = s.races[rid]
    s.ingest_odds({"race": rid, "stage": stage, "ended": ended, "announce_raw": announce.strftime("%m%d%H%M"),
                   "values": dict.fromkeys(COMBOS, "18.3") if values is None else values, "result": "success",
                   "skew": skew, "obs_id": obs_id or s.new_obs_id(), "target": r.key, "adapter": "fictional-noa"})


def day_fetch(s, day, ended, scratches, **kw):
    ev = {"date": day, "ended": ended, "result": "success", "scope": "day", "complete": True, "reached_end": True,
          "parse_errors": 0, "quarantined": 0, "scratches": scratches}
    ev.update(kw)
    s.fetch_day(ev)


def preds(s):
    return s.predictions.rows()


def check(ident, kind, expected, fn):
    try:
        actual, ok = fn()
        row = {"id": ident, "kind": kind, "expected": expected, "actual": actual, "result": "passed" if ok else "failed"}
    except Exception as ex:
        row = {"id": ident, "kind": kind, "expected": expected, "result": "error", "error": repr(ex), "traceback": traceback.format_exc()}
    RESULTS.append(row)
    (P / "new-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str))
    print(ident, kind, row["result"], flush=True)


# ---------------------------------------------------------------- KD-01
def kd01_omitted():
    s = fresh(); t = s.schedule("R05", "15m")
    vals = dict.fromkeys(COMBOS, "18.3")
    for c in [x for x in COMBOS if x not in BETS][:120]:
        del vals[c]
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3), values=vals)
    s.confirm("R05", "15m", t); p = preds(s)[-1]
    return {"rate": p["missing_rate"], "state": p["race_state"], "conf": p["confidence"]}, \
        p["missing_rate"] == [120, 560] and p["race_state"] == "未配分" and p["confidence"] == "Z"


def kd01_unknown_keys():
    s = fresh(); t = s.schedule("R05", "15m")
    vals = dict.fromkeys(COMBOS, "18.3")
    for c in [x for x in COMBOS if x not in BETS][:20]:
        del vals[c]
    vals.update({"1-2-17": "9.9", "3-3-4": "9.9", "0-1-2": "9.9"})   # 出走表にない馬番・重複・対象外
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3), values=vals)
    s.confirm("R05", "15m", t); p = preds(s)[-1]
    return {"rate": p["missing_rate"], "cap": p["confidence_cap"], "state": p["race_state"]}, \
        p["missing_rate"] == [20, 560] and p["confidence_cap"] is None and p["race_state"] == "配分済み"


def kd01_control_complete():
    s = fresh(); t = s.schedule("R05", "15m")
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3))
    s.confirm("R05", "15m", t); p = preds(s)[-1]
    return {"rate": p["missing_rate"], "state": p["race_state"]}, p["missing_rate"] == [0, 560] and p["race_state"] == "配分済み"


# ---------------------------------------------------------------- KD-02
def scratch7(at):
    return [{"race": "R05", "horse": 7, "kind": "出走取消", "announce": at}]


def kd02_halt_blocks_withdraw():
    s = fresh()
    day_fetch(s, D1, D1.replace(hour=13), scratch7(D1.replace(hour=12, minute=58)))
    odds(s, "R05", D1.replace(hour=13, minute=4), D1.replace(hour=13, minute=3), skew=45)   # 時計ずれで停止
    day_fetch(s, D1, D1.replace(hour=13, minute=8), [])                                    # 停止中の空一覧
    active = s._scratch_active("R05", 7, D1.replace(hour=13, minute=9))
    last = s.obs.rows()[-1]
    return {"halted": s.halted, "withdrawals": len(s.withdrawals), "active": active, "undecidable": last.get("undecidable")}, \
        s.halted and len(s.withdrawals) == 0 and active


def kd02_halt_still_adopts_new_scratch():
    s = fresh()
    odds(s, "R05", D1.replace(hour=13, minute=4), D1.replace(hour=13, minute=3), skew=-40)
    day_fetch(s, D1, D1.replace(hour=13, minute=8), scratch7(D1.replace(hour=13, minute=6)))
    return {"halted": s.halted, "active": s._scratch_active("R05", 7, D1.replace(hour=13, minute=10))}, \
        s.halted and s._scratch_active("R05", 7, D1.replace(hour=13, minute=10))


def kd02_control_withdraw_without_halt():
    s = fresh()
    day_fetch(s, D1, D1.replace(hour=13), scratch7(D1.replace(hour=12, minute=58)))
    day_fetch(s, D1, D1.replace(hour=13, minute=8), [])
    return {"halted": s.halted, "withdrawals": len(s.withdrawals)}, (not s.halted) and len(s.withdrawals) == 1


def kd02_judge_clock_condition_kept():
    s = fresh(); t = s.schedule("R05", "15m")
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3))
    s.judge_clock = {"independent": True, "trusted": False}
    res = s.confirm("R05", "15m", t)
    return {"result": res, "preds": len(preds(s))}, res["status"] == "held" and len(preds(s)) == 0


# ---------------------------------------------------------------- KD-03
def kd03_two_dates():
    s = fresh()
    t1 = s.schedule("R05", "15m"); odds(s, "R05", t1 - timedelta(minutes=2), t1 - timedelta(minutes=3)); s.confirm("R05", "15m", t1)
    s.add_race("R05", D2, D2.replace(hour=15, minute=40))
    t2 = s.schedule("R05", "15m"); odds(s, "R05", t2 - timedelta(minutes=2), t2 - timedelta(minutes=3)); res2 = s.confirm("R05", "15m", t2)
    keys = sorted(p["race_key"] for p in preds(s))
    return {"day2": res2, "keys": keys, "pred_ids": sorted(p["pred_id"] for p in preds(s))}, \
        res2["status"] == "saved" and keys == ["2098-03-14/R05", "2098-03-15/R05"] and len({p["pred_id"] for p in preds(s)}) == 2


def kd03_day2_actual_does_not_rejudge_day1():
    s = fresh()
    t1 = s.schedule("R05", "15m"); odds(s, "R05", t1 - timedelta(minutes=2), t1 - timedelta(minutes=3)); s.confirm("R05", "15m", t1)
    s.add_race("R05", D2, D2.replace(hour=15, minute=40))
    t2 = s.schedule("R05", "15m"); odds(s, "R05", t2 - timedelta(minutes=2), t2 - timedelta(minutes=3)); s.confirm("R05", "15m", t2)
    s.ingest_row({"kind": "actual_post", "race": "R05", "actual": D2.replace(hour=15, minute=40), "saved": D2.replace(hour=15, minute=45)})
    le = s.latest_eligibility()
    p1 = [p for p in preds(s) if p["race_key"] == "2098-03-14/R05"][0]
    e1 = le.get(p1["pred_id"])
    agg = s.aggregate()["forward"]
    return {"day1_latest": e1, "aggregate": {k: agg[k] for k in ("official", "provisional", "unconfirmed")}}, \
        e1 is not None and e1["basis"] != "実際" and e1["post_used"] == D1.replace(hour=15, minute=40) and agg["official"] == 1 and agg["provisional"] == 1


def kd03_settle_uses_dated_race():
    s = fresh()
    t1 = s.schedule("R05", "15m"); odds(s, "R05", t1 - timedelta(minutes=2), t1 - timedelta(minutes=3)); s.confirm("R05", "15m", t1)
    s.add_race("R05", D2, D2.replace(hour=15, minute=40))
    s.ingest_row({"kind": "payout", "race": "R05", "race_key": "2098-03-14/R05", "saved": D1.replace(hour=16, minute=30),
                  "refund_flag": False, "refund_horses": [], "not_established": False, "special": False,
                  "trio": {"combo": "1-2-3", "pay": 4210}})
    st = s.settlements.rows()
    p1 = [p for p in preds(s) if p["race_key"] == "2098-03-14/R05"][0]
    return {"settlements": st}, len(st) == 1 and st[0]["pred_id"] == p1["pred_id"] and st[0]["payout"] == 10 * 4210


# ---------------------------------------------------------------- KD-04
def kd04_save_before_timepoint_held():
    s = fresh(); t = s.schedule("R05", "15m")
    at = t - timedelta(minutes=5)
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3))     # 保存時刻より後の観測
    res = s.confirm("R05", "15m", at)
    ps = preds(s)
    future = [r for p in ps for r in p["refs"] if r["ended"] > at]
    return {"result": res, "preds": len(ps), "future_refs": future, "holds": [h["kind"] for h in s.holds.rows()]}, \
        not future and res["status"] == "held" and len(ps) == 0 and len(s.holds) >= 1


def kd04_control_save_at_timepoint():
    s = fresh(); t = s.schedule("R05", "15m")
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3))
    res = s.confirm("R05", "15m", t)
    p = preds(s)[-1]
    return {"result": res, "refs": p["refs"]}, res["status"] == "saved" and len(p["refs"]) == 1 and p["refs"][0]["ended"] <= t


# ---------------------------------------------------------------- KD-05
def kd05_replay_no_new_warning():
    s = fresh(); t = s.schedule("R05", "15m")
    a = dict.fromkeys(COMBOS, "18.3"); b = dict(a); b["1-2-4"] = "22.6"

    def events():
        odds(s, "R05", t - timedelta(minutes=3), t - timedelta(minutes=4), values=a, obs_id="noa-a")
        odds(s, "R05", t + timedelta(minutes=2), t + timedelta(minutes=1), values=b, obs_id="noa-b")
        s.confirm("R05", "15m", t)
    events(); w1 = len(s.warnings); c1 = len(s.content); p1 = preds(s)[-1]["hash"]; o1 = len(s.obs)
    events(); w2 = len(s.warnings); c2 = len(s.content); p2 = preds(s)[-1]["hash"]; o2 = len(s.obs)
    return {"warnings": [w1, w2], "content": [c1, c2], "hash_same": p1 == p2, "obs": [o1, o2]}, \
        w1 == w2 and c1 == c2 and p1 == p2 and o2 >= o1


def kd05_new_acquisition_still_warns():
    s = fresh(); t = s.schedule("R05", "15m")
    a = dict.fromkeys(COMBOS, "18.3"); b = dict(a); b["1-2-4"] = "22.6"
    odds(s, "R05", t - timedelta(minutes=3), t - timedelta(minutes=4), values=a, obs_id="noa-a")
    s.confirm("R05", "15m", t)
    w0 = len(s.warnings)
    odds(s, "R05", t + timedelta(minutes=3), t + timedelta(minutes=2), values=b, obs_id="noa-c")   # 確定後の新しい取得
    kinds = [w["kind"] for w in s.warnings.rows()]
    return {"warnings_before": w0, "kinds": kinds}, "odds_after_final" in kinds and len(s.warnings) == w0 + 1


# ---------------------------------------------------------------- SD-01
def sd01_basis_refs():
    s = fresh(); t = s.schedule("R05", "15m")
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3)); s.confirm("R05", "15m", t)
    h0 = preds(s)[-1]["hash"]
    s.ingest_row({"kind": "actual_post", "race": "R05", "actual": D1.replace(hour=15, minute=40), "saved": D1.replace(hour=15, minute=44)})
    e1 = s.eligibility.rows()[-1]
    s.ingest_row({"kind": "actual_post", "race": "R05", "actual": D1.replace(hour=15, minute=41), "saved": D1.replace(hour=15, minute=46)})
    e2 = s.eligibility.rows()[-1]
    acts = [b["_seq"] for b in s.body.rows() if b["kind"] == "actual_post"]
    r1 = [x.get("seq") for x in e1.get("basis_refs") or []]
    r2 = sorted(x.get("seq") for x in e2.get("basis_refs") or [])
    return {"e1": e1, "e2": e2, "actual_seqs": acts, "hash_unchanged": preds(s)[-1]["hash"] == h0}, \
        e1["basis"] == "実際" and r1 == [acts[0]] and e2["verdict"] == "未確認" and r2 == sorted(acts) and preds(s)[-1]["hash"] == h0


def sd01_control_schedule_basis():
    s = fresh(); t = s.schedule("R05", "15m")
    odds(s, "R05", t - timedelta(minutes=2), t - timedelta(minutes=3)); s.confirm("R05", "15m", t)
    e = s.eligibility.rows()[-1]
    return {"e": e}, e["basis"] == "予定" and e["verdict"] == "適格"


check("NX-01a-kd01-omitted-120", "target", "行が届かない120組も真欠損に数え120/560、未配分・自信度Z", kd01_omitted)
check("NX-01b-kd01-unknown-keys", "target", "欠けた20組は欠損、出走表にない3キーは分母に入れない → 20/560、上限なし", kd01_unknown_keys)
check("NX-01c-kd01-control", "control", "全560組そろえば0/560・配分済み", kd01_control_complete)
check("NX-02a-kd02-halt-no-withdraw", "target", "時計ずれ停止中の空一覧で取消を撤回しない", kd02_halt_blocks_withdraw)
check("NX-02b-kd02-halt-new-scratch", "control", "停止中でも新しい取消は安全側として採用する", kd02_halt_still_adopts_new_scratch)
check("NX-02c-kd02-control", "control", "停止していなければ完全取得の空一覧で撤回できる", kd02_control_withdraw_without_halt)
check("NX-02d-kd02-judge-clock", "control", "判断時計が信頼できなければ確定保留（安全②の維持）", kd02_judge_clock_condition_kept)
check("NX-03a-kd03-two-dates", "target", "同じR05の別開催日は別の予想（2件、別キー・別pred_id）", kd03_two_dates)
check("NX-03b-kd03-rejudge", "target", "2日目の実発走で1日目の予想を再判定しない", kd03_day2_actual_does_not_rejudge_day1)
check("NX-03c-kd03-settle", "target", "日付つきの払戻は、その日の予想だけを精算する", kd03_settle_uses_dated_race)
check("NX-04a-kd04-save-before-T", "target", "予定時点より前の保存は保留し、保存より未来の観測を使わない", kd04_save_before_timepoint_held)
check("NX-04b-kd04-control", "control", "予定時点ちょうどの保存は、その時点までの観測で保存する", kd04_control_save_at_timepoint)
check("NX-05a-kd05-replay", "target", "同じ事象の再配送で警告が増えない（内容・予想も不変、観測は増えてよい）", kd05_replay_no_new_warning)
check("NX-05b-kd05-new-acq", "control", "確定後の新しい取得は従来どおり警告する", kd05_new_acquisition_still_warns)
check("NX-06a-sd01-basis-refs", "target", "適格性の判定行に根拠行（実発走の行）の不変参照を残す。食い違いでは関与した全行", sd01_basis_refs)
check("NX-06b-sd01-control", "control", "予定だけのときは予定が根拠・適格", sd01_control_schedule_basis)

summary = {"cases": len(RESULTS), "passed": sum(x["result"] == "passed" for x in RESULTS),
           "failed": sum(x["result"] == "failed" for x in RESULTS), "errors": sum(x["result"] == "error" for x in RESULTS),
           "by_kind": {k: {"cases": sum(x["kind"] == k for x in RESULTS), "passed": sum(x["kind"] == k and x["result"] == "passed" for x in RESULTS)}
                       for k in ("target", "control")}}
(P / "new-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False))
