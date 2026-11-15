"""のあの新規境界試験（🤍20261115-9、KR8-01〜05）。架空データのみ・通信なし。
みにの入力（2108年、R44、63.7）・前回までの入力（2105年 R33 52.4／2104年 R21／2098年 R05）とは別にした：2110年9月、R52、オッズ71.6、発走15:40。
期待値は正本（みにの KR8 指摘）と依頼文から修正前に書いた。「対象」は修正前に失敗するはず、「対照」は修正前から成功するはず。
"""
from pathlib import Path
from datetime import datetime, timedelta
from itertools import combinations
import sys, json, traceback

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
from keiba_proto.system import System, H  # noqa: E402

D1 = datetime(2110, 9, 13)
D2 = datetime(2110, 9, 14)
RID = "R52"
COMBOS = ["-".join(map(str, c)) for c in combinations(range(1, 17), 3)]
RESULTS = []


def at(day, h, m):
    return day.replace(hour=h, minute=m)


def fresh(day=D1):
    s = System()
    s.add_race(RID, day, at(day, 15, 40))
    return s


def odds(s, ended):
    s.ingest_odds({"race": RID, "stage": 1, "ended": ended, "announce_raw": (ended - timedelta(minutes=1)).strftime("%m%d%H%M"),
                   "values": dict.fromkeys(COMBOS, "71.6"), "result": "success", "skew": 0,
                   "obs_id": s.new_obs_id(), "target": s.races[RID].key, "adapter": "fictional-noa-r3"})


def save(s, late=None):
    t = s.schedule(RID, "15m")
    odds(s, t - timedelta(minutes=2))
    s.confirm(RID, "15m", late or t)
    return s.predictions.rows()[-1]


def actual(s, a, saved, **kw):
    ev = {"kind": "actual_post", "race": RID, "actual": a, "saved": saved}
    ev.update(kw)
    s.ingest_row(ev)
    return ev


def payout(s, saved, **kw):
    ev = {"kind": "payout", "race": RID, "saved": saved, "refund_flag": False, "refund_horses": [], "not_established": False,
          "special": False, "trio": {"combo": "1-2-3", "pay": 5260}}
    ev.update(kw)
    s.ingest_row(ev)
    return ev


def le(s, p):
    return s.latest_eligibility()[p["pred_id"]]


def resolve(s, ref):
    store = getattr(s, ref.get("store", ""), None)
    rows = store.rows() if store is not None and hasattr(store, "rows") else []
    return [r for r in rows if r.get("_seq") == ref.get("seq")]


def check(ident, kind, expected, fn):
    try:
        actual_v, ok = fn()
        row = {"id": ident, "kind": kind, "expected": expected, "actual": actual_v, "result": "passed" if ok else "failed"}
    except Exception as ex:
        row = {"id": ident, "kind": kind, "expected": expected, "result": "error", "error": repr(ex), "traceback": traceback.format_exc()}
    RESULTS.append(row)
    (P / "boundary9-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1, default=str))
    print(ident, kind, row["result"], flush=True)


# ---------------------------------------------------------------- KR8-01
def k1_payout_then_other_day_confirm():
    s = fresh(); p = save(s)
    payout(s, at(D1, 16, 10))                                   # 日付キーなし・候補1つ → 1日目を精算
    s.add_race(RID, D2, at(D2, 15, 40)); q = save(s)            # 払戻行が本文にある状態で別日の確定保存
    preds = {x["pred_id"] for x in s.predictions.rows()}
    judged = {e["pred_id"] for e in s.eligibility.rows()}
    return {"settlements": len(s.settlements), "preds": sorted(preds), "judged": sorted(judged)}, \
        len(s.settlements) == 1 and preds == judged and q["race_key"] != p["race_key"]


def k1_correction_then_actual():
    s = fresh(); p = save(s)
    actual(s, at(D1, 15, 40), at(D1, 15, 46))
    s.ingest_row({"kind": "finish_correction", "race": RID, "saved": at(D1, 15, 58)})
    actual(s, at(D1, 15, 43), at(D1, 16, 0))                    # 着順訂正の後に食い違う実発走
    e = le(s, p)
    return {"verdict": e["verdict"], "basis": e["basis"], "official": s.aggregate()["forward"]["official"]}, \
        e["verdict"] == "未確認" and e["basis"] == "食い違い" and s.aggregate()["forward"]["official"] == 0


def k1_payout_then_postchange_other_day():
    s = fresh(); p = save(s); payout(s, at(D1, 16, 5))
    s.add_race(RID, D2, at(D2, 15, 40))
    s.ingest_row({"kind": "post_change", "race": RID, "race_key": "2110-09-14/" + RID, "old": at(D2, 15, 40), "new": at(D2, 15, 55),
                  "announce": at(D2, 11, 0), "saved": at(D2, 11, 2)})
    return {"applied_post": s.races[RID].applied_post}, s.races[RID].applied_post == at(D2, 15, 55)


def k1_unavailable_actual_control():
    s = fresh(); t = s.schedule(RID, "15m"); odds(s, t - timedelta(minutes=2))
    actual(s, at(D1, 15, 40), at(D1, 15, 47))                   # 確定保存の時点ではまだ保存されていない
    s.confirm(RID, "15m", t); p = s.predictions.rows()[-1]
    e = [x for x in s.eligibility.rows() if x["pred_id"] == p["pred_id"]][0]
    return {"basis": e["basis"]}, e["basis"] == "予定"


# ---------------------------------------------------------------- KR8-02
def k2_redelivery_keeps_conflict():
    s = fresh(); p = save(s)
    first = actual(s, at(D1, 15, 40), at(D1, 15, 45), obs_id="noa-a1")
    actual(s, at(D1, 15, 42), at(D1, 15, 49), obs_id="noa-a2")
    before = le(s, p)
    first["obs_id"] = "noa-a1-redelivered"; s.ingest_row(dict(first))
    after = le(s, p)
    return {"before": before["verdict"], "after": after["verdict"], "official": s.aggregate()["forward"]["official"]}, \
        before["verdict"] == "未確認" and after["verdict"] == "未確認" and s.aggregate()["forward"]["official"] == 0


def k2_late_older_saved_fact_kept():
    s = fresh(); p = save(s)
    actual(s, at(D1, 15, 40), at(D1, 15, 52))                   # 先に処理：保存15:52
    actual(s, at(D1, 15, 41), at(D1, 15, 47))                   # 後から届いた：保存15:47（より早い保存・別の事実）
    e = le(s, p)
    return {"verdict": e["verdict"], "basis": e["basis"]}, e["verdict"] == "未確認" and e["basis"] == "食い違い"


def k2_redelivered_ineligible_stays():
    s = fresh(); p = save(s, late=at(D1, 15, 39))               # 発走1分前の保存 → 未確認域
    ev = actual(s, at(D1, 15, 38), at(D1, 15, 44))              # 実発走が保存より前 → 不適格
    actual(s, at(D1, 15, 38), at(D1, 15, 50))                   # 同じ事実の別取得（保存時刻が違う）
    ev2 = dict(ev, obs_id="noa-redeliver"); s.ingest_row(ev2)
    e = le(s, p)
    return {"verdict": e["verdict"]}, e["verdict"] == "不適格"


def k2_single_late_actual_control():
    s = fresh(); p = save(s); h = p["hash"]
    actual(s, at(D1, 15, 40), at(D1, 15, 55))
    e = le(s, p)
    return {"verdict": e["verdict"], "basis": e["basis"], "official": s.aggregate()["forward"]["official"]}, \
        e["verdict"] == "適格" and e["basis"] == "実際" and s.aggregate()["forward"]["official"] == 1 and s.predictions.rows()[-1]["hash"] == h


# ---------------------------------------------------------------- KR8-03
def _two_days_weights(s):
    k1 = s.races[RID].key
    s.add_race(RID, D2, at(D2, 15, 40)); k2 = s.races[RID].key
    return k1, k2


def k3_view_separates_days():
    s = fresh(); k1, k2 = _two_days_weights(s)
    for kind in ("weight", "race_detail", "entries"):
        for day, key in ((D1, k1), (D2, k2)):
            s.ingest_row({"kind": kind, "race": RID, "race_key": key, "saved": at(day, 14, 0), "announce": at(day, 13, 58), "created": day,
                          "field_size": 16, "horses": list(range(1, 17))})
    got = {kind: [r["race_key"] for r in s.feature_rows(RID, at(D2, 15, 0), kind)] for kind in ("weight", "race_detail", "entries")}
    return got, all(v == [k2] for v in got.values())


def k3_old_day_late_arrival():
    s = fresh(); k1, k2 = _two_days_weights(s)
    s.ingest_row({"kind": "weight", "race": RID, "race_key": k1, "saved": at(D2, 9, 0), "announce": at(D1, 13, 58)})   # 旧日の行が後着
    got = [r["race_key"] for r in s.feature_rows(RID, at(D2, 15, 0), "weight")]
    return {"day2_view": got}, got == []


def k3_view_for_explicit_key():
    s = fresh(); k1, k2 = _two_days_weights(s)
    s.ingest_row({"kind": "weight", "race": RID, "race_key": k1, "saved": at(D1, 14, 0), "announce": at(D1, 13, 58)})
    got = [r["race_key"] for r in s.feature_rows(RID, at(D1, 15, 0), "weight", race_key=k1)]
    return {"day1_view": got}, got == [k1]


def k3_result_by_date_control():
    s = fresh()
    s.ingest_row({"kind": "result", "race_date": D1 - timedelta(days=7), "race_no": 4, "saved": D1 - timedelta(days=7, hours=-20)})
    got = s.feature_rows(RID, at(D1, 15, 0), "result", (D1 - timedelta(days=7)).strftime("%Y-%m-%d"))
    return {"rows": len(got)}, len(got) == 1


# ---------------------------------------------------------------- KR8-04
def k4_postponed_with_actual():
    s = fresh(); post = s.races[RID].post; new = post + timedelta(minutes=40)
    s.ingest_row({"kind": "post_change", "race": RID, "old": post, "new": new, "announce": at(D1, 12, 0), "saved": at(D1, 12, 1)})
    p = save(s); actual(s, new, new + timedelta(minutes=4))
    payout(s, post + timedelta(minutes=15), race_key=p["race_key"])     # 元の予定より後・延期後の発走より前
    return {"settlements": s.settlements.rows(), "quarantine": len(s.quarantine)}, len(s.settlements) == 0 and len(s.quarantine) >= 1


def k4_postponed_without_actual():
    s = fresh(); post = s.races[RID].post; new = post + timedelta(minutes=40)
    s.ingest_row({"kind": "post_change", "race": RID, "old": post, "new": new, "announce": at(D1, 12, 0), "saved": at(D1, 12, 1)})
    p = save(s)
    payout(s, post + timedelta(minutes=15))
    return {"settlements": s.settlements.rows(), "quarantine": len(s.quarantine)}, len(s.settlements) == 0 and len(s.quarantine) >= 1


def k4_payout_before_prediction():
    s = fresh(); p = save(s, late=at(D1, 16, 30))                   # 発走後に作られた予想（不適格）
    payout(s, at(D1, 16, 10))                                       # 発走後だが予想の作成より前に保存された払戻
    return {"settlements": s.settlements.rows(), "holds": [h["kind"] for h in s.holds.rows()]}, \
        len(s.settlements) == 0 and any(h["kind"] == "payout_before_prediction" for h in s.holds.rows())


def k4_legit_late_and_correction_control():
    s = fresh(); post = s.races[RID].post; new = post + timedelta(minutes=40)
    s.ingest_row({"kind": "post_change", "race": RID, "old": post, "new": new, "announce": at(D1, 12, 0), "saved": at(D1, 12, 1)})
    p = save(s); actual(s, new, new + timedelta(minutes=4))
    payout(s, new + timedelta(minutes=20))
    payout(s, new + timedelta(minutes=50), correction=True, trio={"combo": "1-2-3", "pay": 5310})
    types = [x["type"] for x in s.settlements.rows()]
    return {"types": types}, types == ["元", "訂正"]


# ---------------------------------------------------------------- KR8-05
def k5_reregister_keeps_final():
    s = fresh(); p = save(s); fa = s.races[RID].final_at
    s.add_race(RID, D1, at(D1, 15, 50))
    return {"final_at": s.races[RID].final_at}, s.races[RID].final_at == fa and fa is not None


def k5_reregister_then_changes_warn():
    s = fresh(); p = save(s); h = H(s.predictions.rows())
    s.add_race(RID, D1, at(D1, 15, 50))
    s.ingest_row({"kind": "track", "race": RID, "value": "稍重", "old": "良", "saved": at(D1, 15, 31)})
    s.ingest_row({"kind": "post_change", "race": RID, "old": at(D1, 15, 50), "new": at(D1, 16, 5), "announce": at(D1, 15, 32), "saved": at(D1, 15, 33)})
    kinds = {w["kind"] for w in s.warnings.rows()}
    return {"warnings": sorted(kinds), "pred_same": H(s.predictions.rows()) == h}, \
        {"track_after_final", "post_change_after_final"} <= kinds and H(s.predictions.rows()) == h


def k5_reregister_after_final_not_applied():
    s = fresh(); p = save(s); e = le(s, p); ap = s.races[RID].applied_post
    s.add_race(RID, D1, at(D1, 15, 50))
    old = resolve(s, e["basis_refs"][0])
    return {"applied_post": s.races[RID].applied_post, "old_ref": old, "elig_same": le(s, p) == e,
            "warn": [w["kind"] for w in s.warnings.rows()]}, \
        s.races[RID].applied_post == ap and len(old) == 1 and old[0]["post"] == at(D1, 15, 40) and le(s, p) == e \
        and any(w["kind"] == "schedule_after_final" for w in s.warnings.rows())


def k5_reregister_before_final_control():
    s = fresh(); s.add_race(RID, D1, at(D1, 15, 55))
    vers = [x["version"] for x in s.schedules.rows() if x["race_key"] == s.races[RID].key]
    return {"applied_post": s.races[RID].applied_post, "versions": vers}, s.races[RID].applied_post == at(D1, 15, 55) and vers == [1, 2]


check("NZ-01a-kr801-payout-then-confirm", "target", "払戻行がある状態で別日の確定保存が例外なく完了し、全予想に適格性行がある", k1_payout_then_other_day_confirm)
check("NZ-01b-kr801-correction-then-actual", "target", "着順訂正の後の食い違う実発走で例外を出さず、未確認・正式0", k1_correction_then_actual)
check("NZ-01c-kr801-payout-then-postchange", "target", "払戻行がある状態で別日の時刻変更を反映できる", k1_payout_then_postchange_other_day)
check("NZ-01d-kr801-unavailable-control", "control", "確定保存の時点で未保存の実発走は判定に使わない", k1_unavailable_actual_control)
check("NZ-02a-kr802-redelivery-conflict", "target", "既知の食い違いは、旧事象の再配送で巻き戻らない", k2_redelivery_keeps_conflict)
check("NZ-02b-kr802-late-older-saved", "target", "保存時刻がより早い別の事実が後から届いても捨てず、食い違いにする", k2_late_older_saved_fact_kept)
check("NZ-02c-kr802-redelivery-ineligible", "control", "不適格は再配送で変わらない", k2_redelivered_ineligible_stays)
check("NZ-02d-kr802-single-late-control", "control", "正当な後着の実発走は採用し、本文不変", k2_single_late_actual_control)
check("NZ-03a-kr803-view-days", "target", "体重・詳細・出走表の特徴は当該開催キーの行だけ", k3_view_separates_days)
check("NZ-03b-kr803-old-day-late", "target", "旧日の行が後着しても新しい日の特徴に入らない", k3_old_day_late_arrival)
check("NZ-03c-kr803-explicit-key", "target", "開催キーを指定すれば旧日の特徴を取り出せる", k3_view_for_explicit_key)
check("NZ-03d-kr803-result-date-control", "control", "過去結果は結果の日付で選ぶ（開催キーと区別）", k3_result_by_date_control)
check("NZ-04a-kr804-postponed-actual", "target", "延期後の実発走より前に保存された払戻は精算せず隔離する", k4_postponed_with_actual)
check("NZ-04b-kr804-postponed-no-actual", "target", "延期後の適用発走より前に保存された払戻は精算せず隔離する（実発走未着）", k4_postponed_without_actual)
check("NZ-04c-kr804-before-prediction", "target", "予想の作成より前に保存された払戻は精算せず保留する", k4_payout_before_prediction)
check("NZ-04d-kr804-legit-control", "control", "延期後の正当な払戻と訂正は精算する", k4_legit_late_and_correction_control)
check("NZ-05a-kr805-keep-final", "target", "同じキーの予定再登録で確定状態を保つ", k5_reregister_keeps_final)
check("NZ-05b-kr805-warn-after-final", "target", "再登録後も確定後の馬場・時刻変更を警告し、予想本文は不変", k5_reregister_then_changes_warn)
check("NZ-05c-kr805-not-applied", "target", "確定後の予定再登録は適用発走に反映せず警告し、旧予定参照と判定を保つ", k5_reregister_after_final_not_applied)
check("NZ-05d-kr805-before-final-control", "control", "確定前の予定再登録は反映し、版が上がる", k5_reregister_before_final_control)

summary = {"cases": len(RESULTS), "passed": sum(x["result"] == "passed" for x in RESULTS),
           "failed": sum(x["result"] == "failed" for x in RESULTS), "errors": sum(x["result"] == "error" for x in RESULTS),
           "by_kind": {k: {"cases": sum(x["kind"] == k for x in RESULTS), "passed": sum(x["kind"] == k and x["result"] == "passed" for x in RESULTS)}
                       for k in ("target", "control")}}
(P / "boundary9-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False))
