"""のあの新規境界・新仕様試験（🤍20261115-12、KR11-01〜03 と承認方針）。架空データのみ・通信なし。
入力はみに（2120年うるう日、R104、128.6）・前回まで（2118年 R93 117.2 ほか）とは別：2122年10月、R115、オッズ141.9、発走15:30。
期待値は正本（みにの KR11 指摘）と依頼文の承認方針から修正前に書いた。「対象」は修正前に失敗するはず、「対照」は修正前から成功するはず。
新仕様（承認根拠）：
  - 停止中の新規取消だけ例外採用（質問1794719043.593129への回答1794719236.789459、配送スレッド1794719351.165989）
  - 確定後も本文・旧判定・旧参照を保持し、現在の適格性だけ追記再判定（同上）
  - 観測時刻不明版の扱いは案C：予定の更新は観測時刻を必須とし、不明の更新は隔離（本人委任、🤍20261115-12 依頼文）
故障注入は、この試験の中で作った System インスタンスの保存メソッドだけを一時的に失敗させる模擬故障。
"""
from pathlib import Path
from datetime import datetime, timedelta
from itertools import combinations
import sys, json, copy, traceback

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
from keiba_proto.system import System, H  # noqa: E402

D = datetime(2122, 10, 10)
RID = "R115"
POST = D.replace(hour=15, minute=30)
COMBOS = ["-".join(map(str, c)) for c in combinations(range(1, 17), 3)]
RESULTS = []


def hm(h, m):
    return D.replace(hour=h, minute=m)


def fresh(observed_at=None):
    s = System()
    s.add_race(RID, D, POST, observed_at=observed_at)
    return s


def odds(s, ended, skew=0):
    s.ingest_odds({"race": RID, "stage": 1, "ended": ended, "announce_raw": (ended - timedelta(minutes=1)).strftime("%m%d%H%M"),
                   "values": dict.fromkeys(COMBOS, "141.9"), "result": "success", "skew": skew,
                   "obs_id": s.new_obs_id(), "target": s.races[RID].key, "adapter": "fictional-noa-r6"})


def save(s, tp="15m"):
    t = s.schedule(RID, tp)
    odds(s, t - timedelta(minutes=2))
    s.confirm(RID, tp, t)
    return s.predictions.rows()[-1]


def le(s, p):
    return s.latest_eligibility()[p["pred_id"]]


def fail_once(obj, name, after=False):
    orig = getattr(obj, name)
    st = {"n": 0}

    def f(*a, **kw):
        st["n"] += 1
        if st["n"] == 1:
            if after:
                orig(*a, **kw)
            raise RuntimeError("模擬故障 %s" % name)
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
    (P / "boundary12-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str))
    print(ident, kind, row["result"], flush=True)


# ---------------------------------------------------------------- KR11-01
def k1_distinct_same_content_conflicts():
    s = fresh()
    for at in (hm(12, 0), hm(12, 9)):
        s.add_race(RID, D, hm(15, 30), observed_at=at); s.add_race(RID, D, hm(15, 44), observed_at=at)
    ws = [w for w in s.warnings.rows() if w["kind"] == "schedule_conflict"]
    return {"conflict_warnings": len(ws)}, len(ws) == 2


def k1_warning_has_event_key():
    s = fresh(); p = save(s)
    s.ingest_row({"kind": "track", "race": RID, "value": "重", "old": "良", "saved": hm(15, 20)})
    ws = [w for w in s.warnings.rows() if w["kind"] == "track_after_final"]
    return {"rows": ws}, len(ws) == 1 and bool(ws[0].get("event_key"))


def k1_same_event_after_write_control():
    s = fresh(); t = s.schedule(RID, "15m")                     # オッズなし → 未配分警告
    restore = fail_once(s.warnings, "append", after=True)
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError:
        pass
    restore()
    for _ in range(2):
        s.confirm(RID, "15m", t)
    n = sum(w["kind"] == "unallocated" for w in s.warnings.rows())
    return {"unallocated": n}, n == 1


# ---------------------------------------------------------------- KR11-02・案C
def k2_unobserved_update_quarantined():
    s = fresh(); p = save(s, "1h")                               # 1h 14:30 保存
    n_sched, e0 = len(s.schedules), le(s, p)
    s.add_race(RID, D, hm(14, 25))                               # 観測時刻のない予定更新
    e = le(s, p)
    return {"schedules_added": len(s.schedules) - n_sched, "quarantine": [q["reason"] for q in s.quarantine.rows()],
            "holds": [h["kind"] for h in s.holds.rows()], "verdict": e["verdict"], "same": e == e0}, \
        len(s.schedules) == n_sched and any("観測時刻" in q["reason"] for q in s.quarantine.rows()) \
        and any(h["kind"] == "schedule_update_unobserved" for h in s.holds.rows()) and e == e0


def k2_unobserved_update_not_applied():
    s = fresh()                                                  # 初期登録（観測時刻なし）は受け付ける
    s.add_race(RID, D, hm(15, 50))                               # 観測時刻なしの更新は採用しない
    return {"post": s.races[RID].post, "applied": s.races[RID].applied_post}, \
        s.races[RID].post == POST and s.races[RID].applied_post == POST


def k2_initial_registration_control():
    s = fresh(); p = save(s, "1h"); e = le(s, p)
    return {"basis": e["basis"], "verdict": e["verdict"]}, e["basis"] == "予定" and e["verdict"] == "適格"


def k2_same_observation_not_new():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(15, 40), observed_at=hm(14, 40)); ns, ne = len(s.schedules), len(s.eligibility)
    s.add_race(RID, D, hm(15, 40), observed_at=hm(14, 40))      # 同じ観測の再登録
    return {"schedules_added": len(s.schedules) - ns, "elig_added": len(s.eligibility) - ne}, \
        len(s.schedules) == ns and len(s.eligibility) == ne


def k2_future_info_control():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(14, 28), observed_at=hm(14, 45))
    first = [e for e in s.eligibility.rows() if e["pred_id"] == p["pred_id"]][0]
    return {"first_basis": first["basis"], "first_post": first["post_used"]}, first["post_used"] == POST


def k2_same_time_conflict_control():
    s = fresh(); p = save(s, "1h")
    s.add_race(RID, D, hm(14, 20), observed_at=hm(14, 40)); s.add_race(RID, D, hm(15, 45), observed_at=hm(14, 40))
    e = le(s, p)
    return {"verdict": e["verdict"], "basis": e["basis"]}, e["verdict"] == "未確認" and e["basis"] == "食い違い"


# ---------------------------------------------------------------- KR11-03
def k3_conflict_refs_grow():
    s = fresh(); p = save(s, "1h"); at = hm(14, 40)
    for post in (hm(15, 30), hm(15, 42), hm(15, 54)):
        s.add_race(RID, D, post, observed_at=at)
    e = le(s, p)
    involved = {x["_seq"] for x in s.schedules.rows() if x["race_key"] == p["race_key"] and x["observed_at"] == at}
    return {"refs": [r["seq"] for r in e["basis_refs"]], "involved": sorted(involved)}, \
        e["verdict"] == "未確認" and {r["seq"] for r in e["basis_refs"]} == involved


def k3_redelivery_no_extra_rows_control():
    s = fresh(); p = save(s, "1h"); at = hm(14, 40)
    s.add_race(RID, D, hm(15, 30), observed_at=at); s.add_race(RID, D, hm(15, 42), observed_at=at)
    n = len(s.eligibility)
    s.add_race(RID, D, hm(15, 42), observed_at=at)              # 同じ事象の再配送
    return {"added": len(s.eligibility) - n}, len(s.eligibility) == n


# ---------------------------------------------------------------- 承認1：停止中の新規取消だけ例外採用
def _halt(s, at):
    odds(s, at, skew=-55)


def k4_halted_new_scratch_adopted():
    s = fresh(); _halt(s, hm(13, 0))
    s.fetch_day({"date": D, "ended": hm(13, 5), "result": "success", "scope": "day", "complete": True, "reached_end": True,
                 "parse_errors": 0, "quarantined": 0, "scratches": [{"race": RID, "horse": 7, "kind": "出走取消", "announce": hm(13, 2)}]})
    return {"active": s._scratch_active(RID, 7, hm(13, 10)), "halted": s.halted}, s.halted and s._scratch_active(RID, 7, hm(13, 10))


def k4_halted_other_info_quarantined_control():
    s = fresh(); p = save(s); _halt(s, hm(15, 20))
    n = len(s.body)
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": POST, "saved": hm(15, 36)})
    return {"body_added": len(s.body) - n}, len(s.body) == n


def k4_halted_no_withdraw_control():
    s = fresh()
    s.fetch_day({"date": D, "ended": hm(12, 0), "result": "success", "scope": "day", "complete": True, "reached_end": True,
                 "parse_errors": 0, "quarantined": 0, "scratches": [{"race": RID, "horse": 7, "kind": "出走取消", "announce": hm(11, 58)}]})
    _halt(s, hm(13, 0))
    s.fetch_day({"date": D, "ended": hm(13, 5), "result": "success", "scope": "day", "complete": True, "reached_end": True,
                 "parse_errors": 0, "quarantined": 0, "scratches": []})
    return {"active": s._scratch_active(RID, 7, hm(13, 10))}, s._scratch_active(RID, 7, hm(13, 10))


# ---------------------------------------------------------------- 承認2：確定後も現在の適格性だけ追記再判定
def k5_after_final_rejudge():
    s = fresh(); p = save(s); old = s.eligibility.rows(); body = H(s.predictions.rows())
    s.add_race(RID, D, hm(15, 14), observed_at=hm(15, 20))      # 確定（15:15）後に、保存より前の予定を観測
    e = le(s, p)
    old_ref = s.schedules.rows()[old[-1]["basis_refs"][0]["seq"]]
    return {"verdict": e["verdict"], "applied": s.races[RID].applied_post, "old_ref_post": old_ref["post"]}, \
        e["verdict"] == "不適格" and s.races[RID].applied_post == POST and s.eligibility.rows()[:len(old)] == old \
        and H(s.predictions.rows()) == body and old_ref["post"] == POST


def k5_default_true():
    return {"default": System().rejudge_schedule_after_final}, System().rejudge_schedule_after_final is True


def k5_after_final_redelivery_control():
    s = fresh(); p = save(s)
    s.add_race(RID, D, hm(15, 40), observed_at=hm(15, 20)); n = len(s.eligibility)
    s.add_race(RID, D, hm(15, 40), observed_at=hm(15, 20))
    return {"added": len(s.eligibility) - n}, len(s.eligibility) == n


check("NR-01a-kr1101-distinct-events", "target", "内容が同じでも別の事象の競合警告は別々に残す", k1_distinct_same_content_conflicts)
check("NR-01b-kr1101-event-key", "target", "警告行に安定した事象キーを残す（表示内容と分離）", k1_warning_has_event_key)
check("NR-01c-kr1101-same-event-control", "control", "同じ事象の保存後例外は、再試行で1件に回復", k1_same_event_after_write_control)
check("NR-02a-kr1102-unobserved-quarantined", "target", "案C：観測時刻のない予定更新は隔離・保留し、予定版にも現在判定にも入れない", k2_unobserved_update_quarantined)
check("NR-02b-kr1102-unobserved-not-applied", "target", "案C：観測時刻のない予定更新を適用中の予定に反映しない（受信順で採用しない）", k2_unobserved_update_not_applied)
check("NR-02c-kr1102-initial-control", "control", "新規の初期登録は観測時刻なしでも受け付ける（境界）", k2_initial_registration_control)
check("NR-02d-kr1102-same-observation", "target", "同じ観測の再登録は新しい根拠として扱わない（予定版・判定を足さない）", k2_same_observation_not_new)
check("NR-02e-kr1102-future-control", "control", "判定時点より後に観測された予定は、その時点の判定に使わない", k2_future_info_control)
check("NR-02f-kr1102-same-time-control", "control", "同じ観測時刻の異なる予定は食い違い・未確認", k2_same_time_conflict_control)
check("NR-03a-kr1103-refs-grow", "target", "競合する根拠が増えたら、現在判定の参照を全根拠に追記する", k3_conflict_refs_grow)
check("NR-03b-kr1103-redelivery-control", "control", "同じ事象の再配送では判定を増やさない", k3_redelivery_no_extra_rows_control)
check("NR-04a-approve1-halted-scratch", "target", "承認：停止中に届いた新しい取消は例外として採用する", k4_halted_new_scratch_adopted)
check("NR-04b-approve1-other-control", "control", "停止中のほかの情報（実発走）は隔離のまま", k4_halted_other_info_quarantined_control)
check("NR-04c-approve1-no-withdraw-control", "control", "停止中の取得で取消を撤回しない", k4_halted_no_withdraw_control)
check("NR-05a-approve2-after-final", "target", "承認：確定後の予定更新で現在の適格性を追記再判定（本文・旧判定・旧参照・適用予定は不変）", k5_after_final_rejudge)
check("NR-05b-approve2-default", "target", "承認：確定後の再判定は既定で有効", k5_default_true)
check("NR-05c-approve2-redelivery-control", "control", "確定後でも同じ更新の再配送では判定を増やさない", k5_after_final_redelivery_control)

summary = {"cases": len(RESULTS), "passed": sum(x["result"] == "passed" for x in RESULTS),
           "failed": sum(x["result"] == "failed" for x in RESULTS), "errors": sum(x["result"] == "error" for x in RESULTS),
           "by_kind": {k: {"cases": sum(x["kind"] == k for x in RESULTS), "passed": sum(x["kind"] == k and x["result"] == "passed" for x in RESULTS)}
                       for k in ("target", "control")}}
(P / "boundary12-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False))
