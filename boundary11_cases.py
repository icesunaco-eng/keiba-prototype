"""のあの新規境界試験（🤍20261115-11、KR10-01〜04）。架空データのみ・通信なし。
みにの入力（2116年8月、R82、104.3、発走19:40）・前回までの入力（2114年 R71 93.5 ほか）とは別にした：2118年5月、R93、オッズ117.2、発走16:15。
期待値は正本（みにの KR10 指摘）と依頼文から修正前に書いた。「対象」は修正前に失敗するはず、「対照」は修正前から成功するはず。
故障注入は、この試験の中で作った System インスタンスの保存メソッド・索引だけを一時的に失敗させる模擬故障で、通常入力で自然に起きる障害ではない。永続化の保証ではない。
"""
from pathlib import Path
from datetime import datetime, timedelta
from itertools import combinations
import sys, json, copy, traceback

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
from keiba_proto.system import System, H  # noqa: E402

D = datetime(2118, 5, 26)
RID = "R93"
POST = D.replace(hour=16, minute=15)
COMBOS = ["-".join(map(str, c)) for c in combinations(range(1, 17), 3)]
RESULTS = []


def hm(h, m):
    return D.replace(hour=h, minute=m)


def fresh(observed_at=None):
    s = System()
    if observed_at is None:
        s.add_race(RID, D, POST)
    else:
        s.add_race(RID, D, POST, observed_at=observed_at)
    return s


def odds(s, ended):
    s.ingest_odds({"race": RID, "stage": 1, "ended": ended, "announce_raw": (ended - timedelta(minutes=1)).strftime("%m%d%H%M"),
                   "values": dict.fromkeys(COMBOS, "117.2"), "result": "success", "skew": 0,
                   "obs_id": s.new_obs_id(), "target": s.races[RID].key, "adapter": "fictional-noa-r5"})


def save(s, tp="15m", with_odds=True):
    t = s.schedule(RID, tp)
    if with_odds:
        odds(s, t - timedelta(minutes=2))
    s.confirm(RID, tp, t)
    return s.predictions.rows()[-1]


def le(s, p):
    return s.latest_eligibility()[p["pred_id"]]


def consistent(s):
    ids = [p["pred_id"] for p in s.predictions.rows()]
    return len(ids) == len(set(ids)) and set(ids) == set(s._pred_index.values()) == set(s.latest_eligibility())


def fail_once(obj, name, after=False, nth=1):
    orig = getattr(obj, name)
    st = {"n": 0}

    def f(*a, **kw):
        st["n"] += 1
        if st["n"] == nth:
            if after:
                orig(*a, **kw)
            raise RuntimeError("模擬故障 %s %s" % (name, "保存後" if after else "保存前"))
        return orig(*a, **kw)
    setattr(obj, name, f)
    return lambda: setattr(obj, name, orig)


def check(ident, kind, expected, fn):
    try:
        v, ok = fn()
        row = {"id": ident, "kind": kind, "expected": expected, "actual": v, "result": "passed" if ok else "failed"}
    except Exception as ex:
        row = {"id": ident, "kind": kind, "expected": expected, "result": "error", "error": repr(ex), "traceback": traceback.format_exc()}
    RESULTS.append(row)
    (P / "boundary11-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str))
    print(ident, kind, row["result"], flush=True)


# ---------------------------------------------------------------- KR10-01（模擬故障）
def _confirm_with_fault(store_name, after, with_odds=True, retries=1):
    s = fresh(); t = s.schedule(RID, "15m")
    if with_odds:
        odds(s, t - timedelta(minutes=2))
    restore = fail_once(getattr(s, store_name), "append", after)
    err = None
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError as ex:
        err = str(ex)
    restore()
    rs = [s.confirm(RID, "15m", t, unknown_result=True) for _ in range(retries)]
    return s, err, rs


def k1_pred_saved_then_error():
    s, err, rs = _confirm_with_fault("predictions", True)
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": POST, "saved": POST + timedelta(minutes=6)})
    return {"err": err, "retry": rs, "preds": len(s.predictions), "official": s.aggregate()["forward"]["official"]}, \
        err is not None and len(s.predictions) == 1 and consistent(s) and s.aggregate()["forward"]["official"] == 1


def k1_index_before_write():
    s = fresh(); t = s.schedule(RID, "15m"); odds(s, t - timedelta(minutes=2))

    class FailIdx(dict):
        armed = True

        def __setitem__(self, k, v):
            if self.armed:
                self.armed = False
                raise RuntimeError("模擬故障 索引 保存前")
            super().__setitem__(k, v)
    s._pred_index = FailIdx(s._pred_index); err = None
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError as ex:
        err = str(ex)
    r = s.confirm(RID, "15m", t)
    return {"err": err, "retry": r, "preds": len(s.predictions)}, err is not None and len(s.predictions) == 1 and consistent(s)


def k1_warning_saved_then_error():
    s, err, rs = _confirm_with_fault("warnings", True, with_odds=False)
    n = sum(w["kind"] == "unallocated" for w in s.warnings.rows())
    return {"err": err, "unallocated_warnings": n}, err is not None and n == 1 and consistent(s)


def k1_pred_before_write_control():
    s, err, rs = _confirm_with_fault("predictions", False)
    return {"err": err, "retry": rs, "preds": len(s.predictions)}, err is not None and len(s.predictions) == 1 and consistent(s)


def k1_repeated_retries():
    s, err, rs = _confirm_with_fault("predictions", True, retries=3)
    return {"retries": rs, "preds": len(s.predictions), "elig": len(s.eligibility)}, \
        len(s.predictions) == 1 and len(s.eligibility) == 1 and consistent(s)


# ---------------------------------------------------------------- KR10-02
def k2_late_old_observation_eligibility():
    s = fresh(); p = save(s, "1h")                               # 1h予想 15:15 保存
    s.add_race(RID, D, hm(15, 12), observed_at=hm(15, 40))      # 新しい観測：予定15:12 → 不適格
    s.add_race(RID, D, POST, observed_at=hm(15, 35))            # 古い観測（15:35）の元予定が遅れて届く
    e = le(s, p)
    return {"verdict": e["verdict"], "post_used": e["post_used"]}, e["verdict"] == "不適格" and e["post_used"] == hm(15, 12)


def k2_late_old_observation_applied():
    s = fresh()
    s.add_race(RID, D, hm(16, 30), observed_at=hm(12, 0))
    s.add_race(RID, D, POST, observed_at=hm(11, 0))             # 古い観測の後着
    return {"post": s.races[RID].post, "applied": s.races[RID].applied_post}, \
        s.races[RID].post == hm(16, 30) and s.races[RID].applied_post == hm(16, 30)


def k2_same_time_conflict():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(15, 10), observed_at=hm(15, 30)); s.add_race(RID, D, hm(16, 20), observed_at=hm(15, 30))
    e = le(s, p)
    return {"verdict": e["verdict"], "basis": e["basis"]}, e["verdict"] == "未確認" and e["basis"] == "食い違い"


def k2_same_time_same_post_control():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(16, 25), observed_at=hm(15, 30)); s.add_race(RID, D, hm(16, 25), observed_at=hm(15, 30))
    e = le(s, p)
    return {"verdict": e["verdict"], "post_used": e["post_used"]}, e["verdict"] == "適格" and e["post_used"] == hm(16, 25)


def k2_unknown_after_known_undecided():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(16, 25), observed_at=hm(15, 30))      # 観測時刻つき
    s.add_race(RID, D, hm(15, 10))                               # その後に観測時刻不明の別予定（優先関係は未決）
    e = le(s, p)
    return {"verdict": e["verdict"], "basis": e["basis"], "holds": [h["kind"] for h in s.holds.rows()]}, \
        e["verdict"] == "未確認" and any(h["kind"] == "schedule_priority_undecided" for h in s.holds.rows())


def k2_unknown_after_unknown_control():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(15, 10))                               # 観測時刻不明どうし：宣言済みの動作（後の登録を使う）
    e = le(s, p)
    return {"verdict": e["verdict"], "post_used": e["post_used"]}, e["verdict"] == "不適格" and e["post_used"] == hm(15, 10)


# ---------------------------------------------------------------- KR10-03
def k3_future_only_schedule():
    s = fresh(observed_at=hm(16, 40))                            # 唯一の予定は16:40に観測（1h時点15:15より後）
    p = save(s, "1h")
    e = le(s, p)
    refs = [s.schedules.rows()[r["seq"]] for r in e["basis_refs"] if r.get("store") == "schedules"]
    return {"state": p["race_state"], "verdict": e["verdict"], "refs": refs}, \
        p["race_state"] != "配分済み" and e["verdict"] == "未確認" and all(x["observed_at"] is None or x["observed_at"] <= e["judged_at"] for x in refs)


def k3_current_schedule_observed_later():
    s = fresh()
    s.add_race(RID, D, hm(16, 25), observed_at=hm(15, 50))       # 適用中の予定は15:50に観測（1h時点15:25より後）
    p = save(s, "1h")
    return {"state": p["race_state"], "holds": [h["kind"] for h in s.holds.rows()]}, \
        p["race_state"] != "配分済み" and any(h["kind"] == "schedule_unavailable_at_timepoint" for h in s.holds.rows())


def k3_refs_not_after_judgement_control():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(16, 20), observed_at=hm(15, 30))
    ok = True
    for e in [x for x in s.eligibility.rows() if x["pred_id"] == p["pred_id"]]:
        for r in e["basis_refs"]:
            if r.get("store") == "schedules":
                ob = s.schedules.rows()[r["seq"]]["observed_at"]
                ok = ok and (ob is None or ob <= e["judged_at"])
    return {"ok": ok}, ok


# ---------------------------------------------------------------- KR10-04
def k4_interrupted_update_retry():
    s = fresh(); p = save(s, "24h"); q = save(s, "1h")
    old = s.eligibility.rows(); body = H(s.predictions.rows())
    new, at = hm(15, 12), hm(15, 33)
    restore = fail_once(s.eligibility, "append", nth=2)
    err = None
    try:
        s.add_race(RID, D, new, observed_at=at)
    except RuntimeError as ex:
        err = str(ex)
    restore()
    s.add_race(RID, D, new, observed_at=at)                      # 同じ更新の再試行
    e1, e2 = le(s, p), le(s, q)
    return {"err": err, "post24": e1["post_used"], "post1": e2["post_used"], "v1": e2["verdict"]}, \
        err is not None and e1["post_used"] == e2["post_used"] == new and e2["verdict"] == "不適格" \
        and s.eligibility.rows()[:len(old)] == old and H(s.predictions.rows()) == body


def k4_completed_update_noop_control():
    s = fresh(); p = save(s, "24h"); q = save(s, "1h")
    s.add_race(RID, D, hm(15, 12), observed_at=hm(15, 33)); n = len(s.eligibility)
    s.add_race(RID, D, hm(15, 12), observed_at=hm(15, 33))
    return {"added": len(s.eligibility) - n}, len(s.eligibility) == n


check("NQ-01a-kr1001-pred-saved-error", "target", "予想の保存後に例外が返っても、再試行で予想を重複作成せず、後着の実発走で正式1件のまま", k1_pred_saved_then_error)
check("NQ-01b-kr1001-index-before", "target", "索引の保存前に中断しても、保存済み予想と照合して重複しない", k1_index_before_write)
check("NQ-01c-kr1001-warning-saved-error", "target", "警告の保存後に例外が返っても、再試行で同じ警告を重複しない", k1_warning_saved_then_error)
check("NQ-01d-kr1001-pred-before-control", "control", "予想の保存前の故障は、再試行で1件だけ保存する", k1_pred_before_write_control)
check("NQ-01e-kr1001-repeated-retry", "target", "結果不明の再試行を繰り返しても予想・適格性は1件ずつ", k1_repeated_retries)
check("NQ-02a-kr1002-late-old-elig", "target", "古い観測の予定が後着しても、適格性の根拠を新しい観測から巻き戻さない", k2_late_old_observation_eligibility)
check("NQ-02b-kr1002-late-old-applied", "target", "古い観測の予定が後着しても、適用中の予定を巻き戻さない", k2_late_old_observation_applied)
check("NQ-02c-kr1002-same-time-conflict", "target", "同じ観測時刻の異なる予定は食い違い・未確認", k2_same_time_conflict)
check("NQ-02d-kr1002-same-time-same-control", "control", "同じ観測時刻・同じ予定の重複は食い違いにしない", k2_same_time_same_post_control)
check("NQ-02e-kr1002-unknown-after-known", "target", "観測時刻つきの後に観測時刻不明の異なる予定が来たら、優先関係未決として未確認・保留", k2_unknown_after_known_undecided)
check("NQ-02f-kr1002-unknown-unknown-control", "control", "観測時刻不明どうしは宣言済みの動作（後の登録を使う）", k2_unknown_after_unknown_control)
check("NQ-03a-kr1003-future-only", "target", "判断時点で利用できる予定がなければ、未来の版へ戻らず未確認・配分しない", k3_future_only_schedule)
check("NQ-03b-kr1003-current-later", "target", "適用中の予定が判断時点より後の観測なら、その時点の予想に配分せず保留を記録", k3_current_schedule_observed_later)
check("NQ-03c-kr1003-refs-control", "control", "判定行の予定参照はすべて判定時点以前の観測", k3_refs_not_after_judgement_control)
check("NQ-04a-kr1004-interrupted-retry", "target", "予定更新の途中失敗後、同じ更新の再試行で未完了の予想だけを補う（本文・旧判定は不変）", k4_interrupted_update_retry)
check("NQ-04b-kr1004-noop-control", "control", "完了済みの同じ更新の再試行では判定を足さない", k4_completed_update_noop_control)

summary = {"cases": len(RESULTS), "passed": sum(x["result"] == "passed" for x in RESULTS),
           "failed": sum(x["result"] == "failed" for x in RESULTS), "errors": sum(x["result"] == "error" for x in RESULTS),
           "by_kind": {k: {"cases": sum(x["kind"] == k for x in RESULTS), "passed": sum(x["kind"] == k and x["result"] == "passed" for x in RESULTS)}
                       for k in ("target", "control")}}
(P / "boundary11-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False))
