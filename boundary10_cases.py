"""のあの新規境界試験（🤍20261115-10、KR9-01〜03）。架空データのみ・通信なし。
みにの入力（2112〜2113年、R63、81.8）・前回までの入力（2110年 R52 71.6 ほか）とは別にした：2114年4月、R71、オッズ93.5、発走14:50。
期待値は正本（みにの KR9 指摘）と依頼文から修正前に書いた。「対象」は修正前に失敗するはず、「対照」は修正前から成功するはず。
故障注入は、この試験の中で作った System インスタンスの保存メソッドだけを一時的に失敗させる模擬故障で、通常入力で自然に起きる障害ではない。
"""
from pathlib import Path
from datetime import datetime, timedelta
from itertools import combinations
import sys, json, copy, traceback

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
from keiba_proto.system import System, H  # noqa: E402

D1 = datetime(2114, 4, 18)
D2 = datetime(2114, 4, 19)
RID = "R71"
COMBOS = ["-".join(map(str, c)) for c in combinations(range(1, 17), 3)]
RESULTS = []


def at(h, m, day=D1):
    return day.replace(hour=h, minute=m)


def fresh():
    s = System()
    s.add_race(RID, D1, at(14, 50))
    return s


def odds_ev(s, ended, obs_id=None):
    return {"race": RID, "stage": 1, "ended": ended, "announce_raw": (ended - timedelta(minutes=1)).strftime("%m%d%H%M"),
            "values": dict.fromkeys(COMBOS, "93.5"), "result": "success", "skew": 0,
            "obs_id": obs_id or s.new_obs_id(), "target": s.races[RID].key, "adapter": "fictional-noa-r4"}


def save(s, tp="15m", when=None, with_odds=True):
    t = s.schedule(RID, tp)
    if with_odds:
        s.ingest_odds(odds_ev(s, t - timedelta(minutes=2)))
    r = s.confirm(RID, tp, when or t)
    return r, s.predictions.rows()[-1] if len(s.predictions) else None


def le(s, p):
    return s.latest_eligibility()[p["pred_id"]]


def consistent(s):
    preds = {p["pred_id"] for p in s.predictions.rows()}
    judged = {e["pred_id"] for e in s.eligibility.rows()}
    idx = set(s._pred_index.values())
    return preds == judged == idx


def check(ident, kind, expected, fn):
    try:
        actual_v, ok = fn()
        row = {"id": ident, "kind": kind, "expected": expected, "actual": actual_v, "result": "passed" if ok else "failed"}
    except Exception as ex:
        row = {"id": ident, "kind": kind, "expected": expected, "result": "error", "error": repr(ex), "traceback": traceback.format_exc()}
    RESULTS.append(row)
    (P / "boundary10-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str))
    print(ident, kind, row["result"], flush=True)


# ---------------------------------------------------------------- KR9-01（模擬故障）
def _fail_once(obj, name, msg):
    orig = getattr(obj, name)
    state = {"n": 0}

    def f(*a, **kw):
        if state["n"] == 0:
            state["n"] += 1
            raise RuntimeError(msg)
        return orig(*a, **kw)
    setattr(obj, name, f)
    return lambda: setattr(obj, name, orig)


def k1_elig_append_fault_retry():
    s = fresh(); t = s.schedule(RID, "15m"); s.ingest_odds(odds_ev(s, t - timedelta(minutes=2)))
    restore = _fail_once(s.eligibility, "append", "模擬故障：適格性の保存")
    err = None
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError as ex:
        err = str(ex)
    restore()
    r = s.confirm(RID, "15m", t)
    p = s.predictions.rows()[-1]
    return {"err": err, "retry": r, "consistent": consistent(s), "final_at": s.races[RID].final_at, "elig": len(s.eligibility)}, \
        err is not None and consistent(s) and len(s.predictions) == 1 and len(s.eligibility) == 1 and s.races[RID].final_at == p["at"] \
        and r["status"] in ("saved", "exists")


def k1_warning_fault_retry():
    s = fresh(); t = s.schedule(RID, "15m")                     # オッズなし → 未配分 → 未配分の警告を出す確定保存
    restore = _fail_once(s.warnings, "append", "模擬故障：警告の保存")
    err = None
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError as ex:
        err = str(ex)
    restore()
    r = s.confirm(RID, "15m", t)
    kinds = [w["kind"] for w in s.warnings.rows()]
    return {"err": err, "retry": r, "consistent": consistent(s), "warnings": kinds}, \
        err is not None and consistent(s) and len(s.eligibility) == 1 and kinds.count("unallocated") == 1


def k1_fault_recorded_as_incomplete():
    s = fresh(); t = s.schedule(RID, "15m"); s.ingest_odds(odds_ev(s, t - timedelta(minutes=2)))
    restore = _fail_once(s.eligibility, "append", "模擬故障：適格性の保存")
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError:
        pass
    restore()
    s.confirm(RID, "15m", t)
    notes = [n["kind"] for n in s.notes.rows()]
    return {"notes": notes}, "confirm_incomplete" in notes and "confirm_repaired" in notes


def k1_prepare_fault_control():
    s = fresh(); t = s.schedule(RID, "15m"); s.ingest_odds(odds_ev(s, t - timedelta(minutes=2)))
    orig = s._eligibility_row
    s._eligibility_row = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("模擬故障：判定の準備"))
    try:
        s.confirm(RID, "15m", t)
    except RuntimeError:
        pass
    left = (len(s.predictions), len(s.eligibility), len(s._pred_index), s.races[RID].final_at)
    s._eligibility_row = orig
    r = s.confirm(RID, "15m", t)
    return {"left": left, "retry": r}, left == (0, 0, 0, None) and r["status"] == "saved" and consistent(s)


def k1_double_confirm_control():
    s = fresh(); r1, p = save(s); n = len(s.eligibility)
    r2 = s.confirm(RID, "15m", p["at"])
    return {"r2": r2, "elig_added": len(s.eligibility) - n}, r2["status"] == "exists" and len(s.eligibility) == n


# ---------------------------------------------------------------- KR9-02
def _final(s):
    r, p = save(s)
    return p


def k2_odds_redelivery_then_schedule():
    s = fresh(); p = _final(s)
    ev = odds_ev(s, at(14, 40)); s.ingest_odds(copy.deepcopy(ev)); s.ingest_odds(dict(copy.deepcopy(ev), obs_id="noa-odds-again"))
    s.add_race(RID, D1, at(15, 5))
    return {"warnings": [w["kind"] for w in s.warnings.rows()]}, any(w["kind"] == "schedule_after_final" for w in s.warnings.rows())


def k2_row_redelivery_then_schedule():
    s = fresh(); p = _final(s)
    ev = {"kind": "track", "race": RID, "value": "重", "old": "稍重", "saved": at(14, 41)}
    s.ingest_row(dict(ev, obs_id="noa-t1")); s.ingest_row(dict(ev, obs_id="noa-t2"))
    s.add_race(RID, D1, at(15, 5))
    return {"warnings": [w["kind"] for w in s.warnings.rows()]}, any(w["kind"] == "schedule_after_final" for w in s.warnings.rows())


def k2_day_redelivery_then_schedule():
    s = fresh(); p = _final(s)
    ev = {"date": D1, "ended": at(14, 42), "result": "success", "scope": "day", "complete": True, "reached_end": True,
          "parse_errors": 0, "quarantined": 0, "scratches": [{"race": RID, "horse": 12, "kind": "出走取消", "announce": at(14, 39)}]}
    s.fetch_day(copy.deepcopy(ev)); s.fetch_day(copy.deepcopy(ev))
    s.add_race(RID, D1, at(15, 5))
    return {"warnings": [w["kind"] for w in s.warnings.rows()]}, any(w["kind"] == "schedule_after_final" for w in s.warnings.rows())


def k2_redelivery_still_suppressed_control():
    s = fresh(); p = _final(s)
    ev = {"kind": "track", "race": RID, "value": "重", "old": "稍重", "saved": at(14, 41)}
    s.ingest_row(dict(ev, obs_id="noa-t1")); s.ingest_row(dict(ev, obs_id="noa-t2"))
    return {"track_warnings": sum(w["kind"] == "track_after_final" for w in s.warnings.rows())}, \
        sum(w["kind"] == "track_after_final" for w in s.warnings.rows()) == 1


# ---------------------------------------------------------------- KR9-03
def k3_before_final_rejudge():
    s = fresh(); r, p = save(s, "1h")                            # 1h予想 13:50 保存、最終15mは未確定
    old_rows = s.eligibility.rows()
    s.add_race(RID, D1, at(13, 48))                              # 同じキーで予定を13:48へ（保存より前）
    e = le(s, p); sch = [x for x in s.schedules.rows() if x["race_key"] == p["race_key"]]
    return {"verdict": e["verdict"], "basis": e["basis"], "ref": e["basis_refs"], "applied_post": s.races[RID].applied_post}, \
        e["verdict"] == "不適格" and e["basis"] == "予定" and e["basis_refs"][0]["seq"] == sch[-1]["_seq"] \
        and s.eligibility.rows()[:len(old_rows)] == old_rows and s.predictions.rows()[-1] == p


def k3_observed_at_recorded():
    s = fresh(); r, p = save(s, "1h")
    s.add_race(RID, D1, at(14, 20), observed_at=at(14, 0))      # 予定の新根拠を観測した時刻を明示
    e = le(s, p); sch = [x for x in s.schedules.rows() if x["race_key"] == p["race_key"]]
    return {"trigger_saved": e.get("trigger_saved"), "judged_at": e["judged_at"], "sched_observed": sch[-1].get("observed_at"), "verdict": e["verdict"]}, \
        e.get("trigger_saved") == at(14, 0) and e["judged_at"] == at(14, 0) and sch[-1].get("observed_at") == at(14, 0) and e["verdict"] == "適格"


def k3_later_schedule_keeps_eligible():
    s = fresh(); r, p = save(s, "1h")
    s.add_race(RID, D1, at(15, 10))                              # 延期方向の予定変更：保存13:50は余裕をもって適格のまま
    e = le(s, p)
    return {"verdict": e["verdict"], "post_used": e["post_used"]}, e["verdict"] == "適格" and e["post_used"] == at(15, 10)


def k3_same_post_no_rejudge_control():
    s = fresh(); r, p = save(s, "1h"); n = len(s.eligibility)
    s.add_race(RID, D1, at(14, 50))
    return {"added": len(s.eligibility) - n}, len(s.eligibility) == n


def k3_after_final_held_control():
    # 確定後の再登録は、旧KD8-09・NZ-05cの期待（現在判定の据置き）を変えない（判断待ち・未適用）
    s = fresh(); r, p = save(s); e = le(s, p)
    s.add_race(RID, D1, at(14, 30))
    return {"same": le(s, p) == e, "applied_post": s.races[RID].applied_post}, le(s, p) == e and s.races[RID].applied_post == at(14, 50)


check("NO-01a-kr901-elig-append-fault", "target", "適格性保存の模擬故障の後、再試行で予想・索引・確定時刻・適格性が整合する", k1_elig_append_fault_retry)
check("NO-01b-kr901-warning-fault", "target", "警告保存の模擬故障の後も、適格性は保存済みで、再試行で未配分の警告を1件だけ回復する", k1_warning_fault_retry)
check("NO-01c-kr901-incomplete-recorded", "target", "途中失敗を confirm_incomplete、再試行の修復を confirm_repaired として記録する", k1_fault_recorded_as_incomplete)
check("NO-01d-kr901-prepare-control", "control", "判定準備の故障では何も残らず、再試行で保存できる（計算先行の維持）", k1_prepare_fault_control)
check("NO-01e-kr901-double-control", "control", "整合済みの予想の再確定は exists で、適格性を足さない", k1_double_confirm_control)
check("NO-02a-kr902-odds-replay", "target", "直前がオッズの再配送でも、新しい予定再登録の警告を出す", k2_odds_redelivery_then_schedule)
check("NO-02b-kr902-row-replay", "target", "直前が非オッズ行の再配送でも、新しい予定再登録の警告を出す", k2_row_redelivery_then_schedule)
check("NO-02c-kr902-day-replay", "target", "直前が日次取得の再配送でも、新しい予定再登録の警告を出す", k2_day_redelivery_then_schedule)
check("NO-02d-kr902-suppress-control", "control", "再配送そのものは新しい警告を出さない（従来どおり）", k2_redelivery_still_suppressed_control)
check("NO-03a-kr903-before-final", "target", "確定前の予定更新で、既存予想の現在適格性を新しい予定で追記判定する（本文・旧判定は不変）", k3_before_final_rejudge)
check("NO-03b-kr903-observed-at", "target", "予定の新根拠の観測時刻を明示でき、判定行と予定行に残る", k3_observed_at_recorded)
check("NO-03c-kr903-later-schedule", "target", "延期方向の予定更新でも新しい予定を根拠に追記判定する", k3_later_schedule_keeps_eligible)
check("NO-03d-kr903-same-post-control", "control", "同じ予定の再登録では判定を足さない", k3_same_post_no_rejudge_control)
check("NO-03e-kr903-after-final-held", "control", "確定後の再登録は現在判定・適用中予定を据え置く（旧期待を維持・判断待ち）", k3_after_final_held_control)

summary = {"cases": len(RESULTS), "passed": sum(x["result"] == "passed" for x in RESULTS),
           "failed": sum(x["result"] == "failed" for x in RESULTS), "errors": sum(x["result"] == "error" for x in RESULTS),
           "by_kind": {k: {"cases": sum(x["kind"] == k for x in RESULTS), "passed": sum(x["kind"] == k and x["result"] == "passed" for x in RESULTS)}
                       for k in ("target", "control")}}
(P / "boundary10-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False))
