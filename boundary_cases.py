"""のあの新規境界試験（🤍20261115-8、KR7-01〜05）。架空データのみ・通信なし。
みにの入力（2104年、R21、37.9）・前回の入力（2098年、R05、18.3）とは別にした：2105年6月、R33、オッズ52.4。
期待値は設計（v0.2・v0.3）とみにの指摘から先に書いた。「対象」は修正前に失敗するはず、「対照」は修正前から成功するはず。
"""
from pathlib import Path
from datetime import datetime, timedelta
from itertools import combinations
import sys, json, copy, traceback

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
from keiba_proto.system import System  # noqa: E402

D1 = datetime(2105, 6, 7)
D2 = datetime(2105, 6, 8)
RID = "R33"
COMBOS = ["-".join(map(str, c)) for c in combinations(range(1, 17), 3)]
RESULTS = []


def fresh(day=D1):
    s = System()
    s.add_race(RID, day, day.replace(hour=16, minute=25))
    return s


def odds(s, ended, announce, values=None, skew=0):
    s.ingest_odds({"race": RID, "stage": 1, "ended": ended, "announce_raw": announce.strftime("%m%d%H%M"),
                   "values": dict.fromkeys(COMBOS, "52.4") if values is None else values, "result": "success", "skew": skew,
                   "obs_id": s.new_obs_id(), "target": s.races[RID].key, "adapter": "fictional-noa-r2"})


def save(s):
    t = s.schedule(RID, "15m")
    odds(s, t - timedelta(minutes=2), t - timedelta(minutes=3))
    s.confirm(RID, "15m", t)
    return s.predictions.rows()[-1]


def halt(s, at):
    odds(s, at, at - timedelta(minutes=1), skew=-48)


def payout(s, saved, **kw):
    ev = {"kind": "payout", "race": RID, "saved": saved, "refund_flag": False, "refund_horses": [], "not_established": False,
          "special": False, "trio": {"combo": "1-2-3", "pay": 3150}}
    ev.update(kw)
    s.ingest_row(ev)


def two():
    s = fresh(); p1 = save(s)
    s.add_race(RID, D2, D2.replace(hour=16, minute=25)); p2 = save(s)
    return s, p1, p2


def resolve(s, ref):
    store = getattr(s, ref.get("store", ""), None)
    rows = store.rows() if store is not None and hasattr(store, "rows") else []
    return [r for r in rows if r.get("_seq") == ref.get("seq")]


def check(ident, kind, expected, fn):
    try:
        actual, ok = fn()
        row = {"id": ident, "kind": kind, "expected": expected, "actual": actual, "result": "passed" if ok else "failed"}
    except Exception as ex:
        row = {"id": ident, "kind": kind, "expected": expected, "result": "error", "error": repr(ex), "traceback": traceback.format_exc()}
    RESULTS.append(row)
    (P / "boundary-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str))
    print(ident, kind, row["result"], flush=True)


# ---------------------------------------------------------------- KR7-01
def k1_empty():
    s = fresh(); t = s.schedule(RID, "15m")
    odds(s, t - timedelta(minutes=2), t - timedelta(minutes=3), values={})
    s.confirm(RID, "15m", t); p = s.predictions.rows()[-1]
    return {"rate": p["missing_rate"], "conf": p["confidence"], "state": p["race_state"], "refs": len(p["refs"])}, \
        p["missing_rate"] == [560, 560] and p["confidence"] == "Z" and p["race_state"] == "未配分" and len(p["refs"]) == 1


def k1_no_observation():
    s = fresh(); t = s.schedule(RID, "15m")
    s.confirm(RID, "15m", t); p = s.predictions.rows()[-1]
    return {"rate": p["missing_rate"], "reasons": p["reasons"], "refs": p["refs"]}, \
        p["missing_rate"] is None and "未取得" in p["reasons"] and p["refs"] == []


# ---------------------------------------------------------------- KR7-02
def k2_actual_while_halted():
    s = fresh(); p = save(s); halt(s, p["at"] + timedelta(minutes=1))
    b0, e0 = len(s.body), len(s.eligibility)
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": D1.replace(hour=16, minute=25), "saved": D1.replace(hour=16, minute=30)})
    agg = s.aggregate()["forward"]
    return {"new_body": len(s.body) - b0, "new_elig": len(s.eligibility) - e0, "official": agg["official"]}, \
        len(s.body) == b0 and len(s.eligibility) == e0 and agg["official"] == 0


def k2_hist_while_halted():
    s = fresh(); halt(s, D1.replace(hour=10))
    s.hist_import({"today": D1.replace(hour=11), "results": [{"race_date": D1 - timedelta(days=7), "race_no": 3}]})
    return {"hist_rows": len(s.hist_rows)}, len(s.hist_rows) == 0


def k2_new_scratch_while_halted():
    s = fresh(); halt(s, D1.replace(hour=15))
    s.fetch_day({"date": D1, "ended": D1.replace(hour=15, minute=5), "result": "success", "scope": "day", "complete": True,
                 "reached_end": True, "parse_errors": 0, "quarantined": 0,
                 "scratches": [{"race": RID, "horse": 9, "kind": "出走取消", "announce": D1.replace(hour=15, minute=3)}]})
    active = s._scratch_active(RID, 9, D1.replace(hour=15, minute=10))
    return {"active": active, "scratch_rows": len([b for b in s.body.rows() if b["kind"] == "scratch"])}, not active


def k2_payout_while_halted():
    s = fresh(); p = save(s); halt(s, p["at"] + timedelta(minutes=1))
    payout(s, D1.replace(hour=17))
    return {"settlements": s.settlements.rows()}, len(s.settlements) == 0


def k2_prehalt_obs_control():
    s = fresh(); t = s.schedule(RID, "15m")
    odds(s, t - timedelta(minutes=2), t - timedelta(minutes=3)); halt(s, t - timedelta(minutes=1))
    s.confirm(RID, "15m", t); p = s.predictions.rows()[-1]
    return {"state": p["race_state"], "refs": p["refs"]}, p["race_state"] == "配分済み" and len(p["refs"]) == 1


# ---------------------------------------------------------------- KR7-03
def k3_unknown_key_actual():
    s, p1, p2 = two(); e2 = s.latest_eligibility()[p2["pred_id"]]
    s.ingest_row({"kind": "actual_post", "race": RID, "race_key": "2105-06-30/" + RID, "actual": D2.replace(hour=16, minute=25),
                  "saved": D2.replace(hour=16, minute=30)})
    return {"day2_unchanged": s.latest_eligibility()[p2["pred_id"]] == e2, "quarantine": len(s.quarantine)}, \
        s.latest_eligibility()[p2["pred_id"]] == e2 and len(s.quarantine) >= 1


def k3_mismatched_rid_key():
    s, p1, p2 = two(); s.add_race("R34", D2, D2.replace(hour=17))
    e2 = s.latest_eligibility()[p2["pred_id"]]
    s.ingest_row({"kind": "actual_post", "race": RID, "race_key": "2105-06-08/R34", "actual": D2.replace(hour=16, minute=25),
                  "saved": D2.replace(hour=16, minute=30)})
    return {"day2_unchanged": s.latest_eligibility()[p2["pred_id"]] == e2, "quarantine": len(s.quarantine)}, \
        s.latest_eligibility()[p2["pred_id"]] == e2 and len(s.quarantine) >= 1


def k3_undated_track_two_dates():
    # 改訂（作業中に1回）：最初の版は日付キーなしの実発走で保留を期待していたが、実発走の時刻は開催日を含み一意に決まる。
    # 指定試験（KD7-05・NX-03b）もその前提なので、日付を持たない馬場変更の行で「一意に決められないなら保留」を確かめる形にした。
    s, p1, p2 = two()
    s.ingest_row({"kind": "track", "race": RID, "value": "不良", "old": "重", "saved": D1.replace(hour=15)})
    tracks = [b for b in s.body.rows() if b["kind"] == "track"]
    return {"track_rows": len(tracks), "holds": [h["kind"] for h in s.holds.rows()]}, \
        len(tracks) == 0 and any(h["kind"] == "race_key_ambiguous" for h in s.holds.rows())


def k3_undated_actual_no_matching_date():
    s, p1, p2 = two(); e1, e2 = s.latest_eligibility()[p1["pred_id"]], s.latest_eligibility()[p2["pred_id"]]
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": (D2 + timedelta(days=1)).replace(hour=16, minute=25),
                  "saved": (D2 + timedelta(days=1)).replace(hour=16, minute=30)})
    le = s.latest_eligibility()
    return {"unchanged": [le[p1["pred_id"]] == e1, le[p2["pred_id"]] == e2], "holds": len(s.holds)}, \
        le[p1["pred_id"]] == e1 and le[p2["pred_id"]] == e2 and len(s.holds) >= 1


def k3_undated_single_control():
    s = fresh(); p = save(s)
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": D1.replace(hour=16, minute=25), "saved": D1.replace(hour=16, minute=30)})
    e = s.latest_eligibility()[p["pred_id"]]
    return {"basis": e["basis"], "verdict": e["verdict"]}, e["basis"] == "実際" and e["verdict"] == "適格"


def k3_older_dated_scratch():
    s = fresh(); k1 = s.races[RID].key
    s.add_race(RID, D2, D2.replace(hour=16, minute=25)); k2 = s.races[RID].key
    s.fetch_day({"date": D1, "ended": D1.replace(hour=15), "result": "success", "scope": "day", "complete": True, "reached_end": True,
                 "parse_errors": 0, "quarantined": 0, "scratches": [{"race": RID, "horse": 5, "kind": "出走取消", "announce": D1.replace(hour=14)}]})
    when = D2.replace(hour=15)
    return {"day1": s._scratch_active(RID, 5, when, k1), "day2": s._scratch_active(RID, 5, when, k2)}, \
        s._scratch_active(RID, 5, when, k1) and not s._scratch_active(RID, 5, when, k2)


def k3_unregistered_day_scratch():
    s = fresh(); k1 = s.races[RID].key; other = D1 + timedelta(days=5)
    s.fetch_day({"date": other, "ended": D1.replace(hour=15), "result": "success", "scope": "day", "complete": True, "reached_end": True,
                 "parse_errors": 0, "quarantined": 0, "scratches": [{"race": RID, "horse": 5, "kind": "出走取消", "announce": D1.replace(hour=14)}]})
    return {"current_active": s._scratch_active(RID, 5, D1.replace(hour=15, minute=1), k1), "quarantine": len(s.quarantine)}, \
        not s._scratch_active(RID, 5, D1.replace(hour=15, minute=1), k1) and len(s.quarantine) >= 1


# ---------------------------------------------------------------- KR7-04
def k4_actual_after_saved():
    s = fresh(); p = save(s)
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": D1.replace(hour=16, minute=25), "saved": D1.replace(hour=16, minute=12)})
    return {"official": s.aggregate()["forward"]["official"], "quarantine": len(s.quarantine)}, \
        s.aggregate()["forward"]["official"] == 0 and len(s.quarantine) >= 1


def k4_change_announce_after_saved():
    s = fresh(); post = s.races[RID].post
    s.ingest_row({"kind": "post_change", "race": RID, "announce": D1.replace(hour=14, minute=30), "saved": D1.replace(hour=14, minute=20),
                  "old": post, "new": post + timedelta(minutes=10)})
    return {"applied_post": s.races[RID].applied_post, "quarantine": len(s.quarantine)}, \
        s.races[RID].applied_post == post and len(s.quarantine) >= 1


def k4_payout_before_post():
    s = fresh(); p = save(s)
    payout(s, D1.replace(hour=16, minute=10))
    return {"settlements": s.settlements.rows(), "quarantine": len(s.quarantine)}, len(s.settlements) == 0 and len(s.quarantine) >= 1


def k4_unavailable_basis_at_confirm():
    s = fresh(); t = s.schedule(RID, "15m")
    odds(s, t - timedelta(minutes=2), t - timedelta(minutes=3))
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": D1.replace(hour=16, minute=25), "saved": D1.replace(hour=16, minute=30)})
    s.confirm(RID, "15m", t); p = s.predictions.rows()[-1]
    e = [x for x in s.eligibility.rows() if x["pred_id"] == p["pred_id"]][-1]
    return {"basis": e["basis"], "judged_at": e["judged_at"]}, e["basis"] != "実際"


def k4_late_actual_control():
    s = fresh(); p = save(s); h = p["hash"]
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": D1.replace(hour=16, minute=25), "saved": D1.replace(hour=16, minute=40)})
    e = s.latest_eligibility()[p["pred_id"]]
    return {"basis": e["basis"], "hash_same": s.predictions.rows()[-1]["hash"] == h}, e["basis"] == "実際" and s.predictions.rows()[-1]["hash"] == h


# ---------------------------------------------------------------- KR7-05
def k5_schedule_ref():
    s = fresh(); p = save(s); e = s.eligibility.rows()[-1]
    m = [resolve(s, r) for r in e["basis_refs"]]
    return {"refs": e["basis_refs"], "resolved": m}, e["basis"] == "予定" and all(len(x) == 1 for x in m) and m[0][0]["post"] == D1.replace(hour=16, minute=25)


def k5_reregister_keeps_old_basis():
    s = fresh(); p = save(s); e_old = s.eligibility.rows()[-1]
    s.add_race(RID, D1, D1.replace(hour=16, minute=45))            # 同じ日付キーで予定を登録し直す
    r_old = resolve(s, e_old["basis_refs"][0])
    s.ingest_row({"kind": "track", "race": RID, "value": "重", "old": "稍重", "saved": D1.replace(hour=15)})
    return {"old_ref_resolves": r_old}, len(r_old) == 1 and r_old[0]["post"] == D1.replace(hour=16, minute=25)


def k5_all_basis_resolve():
    s = fresh(); p = save(s); post = s.races[RID].post
    s.ingest_row({"kind": "post_change", "race": RID, "announce": D1.replace(hour=15), "saved": D1.replace(hour=15, minute=1), "old": post, "new": post})
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": post, "saved": post + timedelta(minutes=5)})
    s.ingest_row({"kind": "actual_post", "race": RID, "actual": post + timedelta(minutes=1), "saved": post + timedelta(minutes=6)})
    rows = [e for e in s.eligibility.rows() if e["pred_id"] == p["pred_id"]]
    res = [(e["basis"], [len(resolve(s, r)) for r in e["basis_refs"]]) for e in rows]
    return {"bases": res}, {b for b, _ in res} >= {"予定", "実際", "食い違い"} and all(all(n == 1 for n in ns) and ns for _, ns in res)


check("NY-01a-kr701-empty-odds", "target", "成功した空のオッズ表は560/560欠損・自信度Z・観測参照1件", k1_empty)
check("NY-01b-kr701-no-obs", "control", "観測がなければ率なし・理由「未取得」・参照なし", k1_no_observation)
check("NY-02a-kr702-actual-halted", "target", "停止中の実発走は隔離し、本文行・適格性・正式集計に使わない", k2_actual_while_halted)
check("NY-02b-kr702-hist-halted", "target", "停止中の過去再構成の取込みは採用しない", k2_hist_while_halted)
check("NY-02c-kr702-scratch-halted", "target", "停止中に届いた新しい取消も隔離し、取消扱いに使わない（例外を採用しない）", k2_new_scratch_while_halted)
check("NY-02d-kr702-payout-halted", "target", "停止中の払戻で精算しない", k2_payout_while_halted)
check("NY-02e-kr702-prehalt-control", "control", "停止前の健全な観測は、信頼できる判断時計で使える", k2_prehalt_obs_control)
check("NY-03a-kr703-unknown-key", "target", "明示した未登録キーの実発走は隔離し、最新日へ付け替えない", k3_unknown_key_actual)
check("NY-03b-kr703-rid-key-mismatch", "target", "別レース番号のキーを明示した行は隔離する", k3_mismatched_rid_key)
check("NY-03c-kr703-undated-two-dates", "target", "同番号2開催があり、行に日付がない（馬場変更）なら一意に決められないので保留する", k3_undated_track_two_dates)
check("NY-03g-kr703-undated-actual-nomatch", "target", "日付キーなしの実発走で、その日付の開催が登録されていなければ保留し、どちらにも付けない", k3_undated_actual_no_matching_date)
check("NY-03d-kr703-undated-single", "control", "開催が1つだけなら日付キーなしでも適用する", k3_undated_single_control)
check("NY-03e-kr703-older-dated-scratch", "control", "登録済みの前日の取消は前日だけに付く", k3_older_dated_scratch)
check("NY-03f-kr703-unregistered-day", "target", "未登録の開催日の取消は隔離し、現在日に付けない", k3_unregistered_day_scratch)
check("NY-04a-kr704-actual-after-saved", "target", "実発走が保存より後の行は隔離し、正式集計に使わない", k4_actual_after_saved)
check("NY-04b-kr704-announce-after-saved", "target", "発表が保存より後の時刻変更は隔離し、予定に反映しない", k4_change_announce_after_saved)
check("NY-04c-kr704-payout-before-post", "target", "発走より前に保存された払戻は隔離し、精算しない", k4_payout_before_post)
check("NY-04d-kr704-unavailable-basis", "target", "確定保存の時点でまだ利用できない実発走を、その時点の判定に使わない", k4_unavailable_basis_at_confirm)
check("NY-04e-kr704-late-actual-control", "control", "正当に後から届いた実発走で再判定し、予想本文は不変", k4_late_actual_control)
check("NY-05a-kr705-schedule-ref", "target", "予定の根拠は保存された予定行へ参照解決できる", k5_schedule_ref)
check("NY-05b-kr705-reregister", "target", "同じ日付キーで予定を登録し直しても、旧判定の根拠行は残る", k5_reregister_keeps_old_basis)
check("NY-05c-kr705-all-basis", "target", "予定・実際・食い違いのすべての根拠参照が保存行へ解決する", k5_all_basis_resolve)

summary = {"cases": len(RESULTS), "passed": sum(x["result"] == "passed" for x in RESULTS),
           "failed": sum(x["result"] == "failed" for x in RESULTS), "errors": sum(x["result"] == "error" for x in RESULTS),
           "by_kind": {k: {"cases": sum(x["kind"] == k for x in RESULTS), "passed": sum(x["kind"] == k and x["result"] == "passed" for x in RESULTS)}
                       for k in ("target", "control")}}
(P / "boundary-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False))
