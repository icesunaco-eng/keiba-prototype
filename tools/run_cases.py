"""固定済みの cases.json を読み、各ケースを新しい System で実行して期待値と照合する（自己試験）。
期待値は変えない。合否はケースごとの checks と、全体の合否基準（gates）の両方で出す。
使い方: python3 -I tools/run_cases.py <cases.json> <出力JSON>
"""
import hashlib, json, sys, traceback
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from keiba_proto.fictional import FictionalAdapter, T          # noqa: E402
from keiba_proto.system import System, H, fmt, horses_of        # noqa: E402
from keiba_proto import config as K                              # noqa: E402


def pred(s, race, tp):
    ps = [p for p in s.predictions.rows() if p["race"] == race and p["tp"] == tp]
    return ps[-1] if ps else None


def elig_rows(s, race, tp):
    p = pred(s, race, tp)
    return [e for e in s.eligibility.rows() if p and e["pred_id"] == p["pred_id"]]


def bet(s, race, tp, combo):
    p = pred(s, race, tp)
    return next((b for b in p["bets"] if b["combo"] == combo), None) if p else None


def metric(s, name, a):
    if name == "view_count":
        return 1 if s.decision_view(a["race"], a["tp"])["usable"] else 0
    if name == "quarantine_count":
        return len(s.quarantine)
    if name == "quarantine_min":
        return len(s.quarantine) >= 1
    if name == "defect_count":
        return len(s.defects)
    if name == "parse_defects":
        return len([d for d in s.defects.rows() if d["kind"] == "parse"])
    if name == "content_count":
        return len([c for c in s.content.rows() if "kind" not in a or c["kind"] == a["kind"]])
    if name == "body_rows":
        return len([b for b in s.body.rows() if b["kind"] == a["kind"]])
    if name in ("obs_count", "obs_count_min"):
        rs = [o for o in s.obs.rows() if ("race" not in a or o.get("race") == a["race"]) and ("kind" not in a or o["kind"] == a["kind"])
              and ("result" not in a or o["result"] == a["result"])]
        return len(rs) if name == "obs_count" else len(rs) >= 1
    if name == "final_store_count":
        return len(s.final)
    if name == "prerelease_count":
        return len(s.prerelease)
    if name == "pred_count":
        return len([p for p in s.predictions.rows() if p["race"] == a["race"]])
    p = pred(s, a["race"], a["tp"]) if "race" in a and "tp" in a else None
    if name == "pred_refs_final":
        fin = {x["obs_id"] for x in s.final.rows()}
        return len([r for r in (p["refs"] if p else []) if r["obs_id"] in fin])
    if name == "pred_ref_count":
        return len(p["refs"]) if p else 0
    if name == "pred_ref_values":
        if not p or not p["refs"]:
            return None
        val = s.content.rows()[p["refs"][0]["content_id"]]["value"]
        from keiba_proto.fictional import make_values
        for seed in ("V", "X", "Y", "Z", "V1", "S"):
            if make_values(seed) == val:
                return seed
        return "?"
    if name == "pred_ref_obs_end":
        return fmt(p["refs"][0]["ended"]) if p and p["refs"] else None
    if name == "pred_ref_announce":
        return fmt(p["refs"][0]["announce"]) if p and p["refs"] else None
    if name == "pred_ref_fields_complete":
        return bool(p and p["refs"]) and all(all(r.get(k) is not None for k in ("target", "stage", "announce", "obs_id")) for r in p["refs"])
    if name == "race_state":
        return p["race_state"] if p else None
    if name == "reasons_include":
        return bool(p) and a["reason"] in p["reasons"]
    if name == "amounts_all_empty":
        return bool(p) and all(b["amount"] is None for b in p["bets"])
    if name == "amounts_all":
        vals = {b["amount"] for b in p["bets"]} if p else set()
        return vals.pop() if len(vals) == 1 else sorted(vals, key=str)
    if name == "bet_state":
        b = bet(s, a["race"], a["tp"], a["combo"]); return b["state"] if b else None
    if name == "bet_reason":
        b = bet(s, a["race"], a["tp"], a["combo"]); return b["reason"] if b else None
    if name == "bets_with_horse":
        return len([b for b in (p["bets"] if p else []) if b["state"] != "取消" and a["horse"] in horses_of(b["combo"])])
    if name == "confidence":
        return p["confidence"] if p else None
    if name == "confidence_cap":
        return p["confidence_cap"] if p else "予想なし"
    if name == "missing_rate":
        return p["missing_rate"] if p else "予想なし"
    if name == "marks_present":
        return bool(p and p["marks"] and p["evaluations"])
    if name == "rebuild":
        return bool(p and p["rebuild"])
    if name == "rebuild_reason_present":
        return bool(p and p["rebuild_reason"])
    if name == "warnings_total":
        return len(s.warnings)
    if name == "warnings_kind":
        return len([w for w in s.warnings.rows() if w["kind"] == a["kind"]])
    if name == "warning_field":
        ws = [w for w in s.warnings.rows() if w["kind"] == a["kind"]]
        return ws[0].get(a["field"]) if ws else None
    if name == "warnings_have_row_time":
        return len(s.warnings) > 0 and all(w.get("row_time") for w in s.warnings.rows())
    if name == "feature_rows":
        at = s.schedule(a["race"], a["tp"]) if "tp" in a else T(a["at"])
        return len(s.feature_rows(a["race"], at, a["kind"], a.get("race_date")))
    if name == "schedule":
        return fmt(s.schedule(a["race"], a["tp"]))
    if name == "pred_hashes_stable":
        return all(H({k: v for k, v in x.items() if k not in ("hash", "_seq")}) == x["hash"] for x in s.predictions.rows())
    if name == "eligibility_latest":
        r = elig_rows(s, a["race"], a["tp"]); return r[-1]["verdict"] if r else None
    if name == "eligibility_first":
        r = elig_rows(s, a["race"], a["tp"]); return r[0]["verdict"] if r else None
    if name == "eligibility_basis":
        r = elig_rows(s, a["race"], a["tp"]); return r[-1]["basis"] if r else None
    if name == "eligibility_rows":
        return len(elig_rows(s, a["race"], a["tp"]))
    agg = s.aggregate()
    if name == "eligible_count":
        return agg["forward"]["official"] + agg["forward"]["provisional"]
    if name in ("official_count", "provisional_count", "unconfirmed_count", "ineligible_count"):
        return agg["forward"][name.replace("_count", "")]
    if name == "agg_skip_count":
        return agg["forward"]["skip"]
    if name == "agg_mixed":
        return len([i for i in agg["forward"]["items"] if i["mode"] != "forward"]) + len([i for i in agg["historical"]["items"] if i["mode"] != "historical"])
    if name == "hist_recovery":
        return agg["historical"]["recovery"]
    if name == "hist_note":
        return agg["historical"]["note"]
    hp = [x for x in s.hist_preds.rows() if x["race"] == a.get("race")]
    if name in ("hist_feature_rows", "hist_feature_rows_min"):
        n = len([f for f in (hp[-1]["features"] if hp else []) if f["race_date"] == a["race_date"]])
        return n if name == "hist_feature_rows" else n >= 1
    if name == "hist_feature_basis_all":
        bs = {f["basis"] for f in (hp[-1]["features"] if hp else [])}
        return bs.pop() if len(bs) == 1 else sorted(bs)
    if name == "hist_odds_refs":
        return len(hp[-1]["odds_refs"]) if hp else None
    if name == "hold_count":
        return len([h for h in s.holds.rows() if h["kind"] == a["kind"]])
    if name == "withdrawal_count":
        return len(s.withdrawals)
    if name == "withdrawal_at":
        w = s.withdrawals.rows(); return fmt(w[0]["at"]) if w else None
    if name == "scratch_rows":
        return len([b for b in s.body.rows() if b["kind"] == "scratch"])
    if name == "undecidable_include":
        return any(u["reason"] == a["reason"] for o in s.obs.rows() if o["kind"] == "day" for u in o["undecidable"])
    st = [x for x in s.settlements.rows() if x["race"] == a.get("race")]
    if name == "settle_rows":
        return len(st)
    if name == "settle_refund":
        return st[-1]["refund"] if st else None
    if name == "settle_invest":
        return st[-1]["invest"] if st else None
    if name == "settle_basis":
        return st[-1]["basis"] if st else None
    if name == "settle_payout_latest":
        return st[-1]["payout"] if st else None
    if name == "settle_payout_first":
        return st[0]["payout"] if st else None
    if name == "note_count":
        return len([n for n in s.notes.rows() if n["kind"] == a["kind"]])
    if name == "halted":
        return s.halted
    if name == "obs_avail_adjust_sec":
        os_ = [o for o in s.obs.rows() if o.get("race") == a["race"] and o["result"] == "success"]
        return os_[-1].get("adjust") if os_ else None
    if name == "obs_announce":
        os_ = [o for o in s.obs.rows() if o.get("race") == a["race"] and o["result"] == "success"]
        return fmt(os_[-1]["announce"]) if os_ else None
    raise KeyError("未知の指標 " + name)


# ---------------------------------------------------------------- 全体の合否基準（v0.2 1〜6 ＋ v0.3 7〜12）
def gates(s):
    g = {}
    obs = {o["obs_id"]: o for o in s.obs.rows() if "obs_id" in o}
    inter = {x["obs_id"] for x in s.intermediate.rows()}
    pre = {x["obs_id"] for x in s.prerelease.rows()}
    bad2, bad7, bad8 = [], [], []
    for p in s.predictions.rows():
        r = s.races[p["race"]]
        acq_max, pub_max = K.FRESHNESS[p["tp"]]
        for ref in p["refs"]:
            o = obs.get(ref["obs_id"])
            if (o is None or o["stage"] != 1 or ref["obs_id"] not in inter or o["result"] != "success"
                    or s.avail_time(o) > p["tp_time"] or p["tp_time"] >= r.applied_post or o["target"] != r.key):
                bad2.append(p["pred_id"])
            if o and ((p["tp_time"] - o["ended"]).total_seconds() + (o.get("adjust") or 0) > acq_max
                      or (p["tp_time"] - o["announce"]).total_seconds() > pub_max):
                bad7.append(p["pred_id"])
            if ref["obs_id"] in pre:
                bad8.append(p["pred_id"])
    g["G2_view_rows_clean"] = not bad2
    g["G7_no_stale_odds"] = not bad7
    g["G8_no_prerelease_final"] = not bad8
    g["G9_pred_hash_unchanged"] = all(H({k: v for k, v in x.items() if k not in ("hash", "_seq")}) == x["hash"] for x in s.predictions.rows())
    okw = True
    for w in s.withdrawals.rows():
        o = obs[w["obs_id"]]
        okw &= (o["result"] == "success" and o["complete"] and o["reached_end"] and not o["parse_errors"] and not o["quarantined"] and o["scope"] == "day")
    g["G10_withdraw_only_complete"] = bool(okw)
    g["G11_unallocated_no_amount"] = all(b["amount"] is None for p in s.predictions.rows() for b in p["bets"] if b["state"] in ("未配分", "取消"))
    g["G12_settle_reads_only_payout"] = all(k in ("payout", "scratch") for (purpose, k) in s.row_access_log if purpose == "settle")
    agg = s.aggregate()
    g["G6_hist_not_mixed"] = (all(i["mode"] == "forward" for i in agg["forward"]["items"]) and
                              all(i["mode"] == "historical" for i in agg["historical"]["items"]))
    return g


def snapshot(s, with_obs=True):
    d = {"content": s.content.rows(), "predictions": s.predictions.rows(), "warnings": s.warnings.rows()}
    if with_obs:
        d.update(obs=s.obs.rows(), quarantine=s.quarantine.rows(), eligibility=s.eligibility.rows(), settlements=s.settlements.rows(),
                 holds=s.holds.rows(), withdrawals=s.withdrawals.rows(), body=s.body.rows())
    return H(d)


def run_events(events, system=None):
    ad = FictionalAdapter(system)
    for ev in events:
        ad.apply(ev)
    return ad.sys


def run_case(c):
    res = {"id": c["id"], "group": c["group"], "checks": [], "error": None}
    try:
        s = run_events(c["events"])
        for ch in c["checks"]:
            got = metric(s, ch["metric"], ch["args"])
            res["checks"].append({"metric": ch["metric"], "args": ch["args"], "expect": ch["expect"], "got": got, "ok": got == ch["expect"]})
        res["gates"] = gates(s)
        # 同じ入力を新しい System で2回 → 完全一致（決定性）
        res["gates"]["G3a_deterministic"] = snapshot(run_events(c["events"])) == snapshot(s)
        # 同じ System にもう一度流す → 内容・予想・警告は増えない（観測ログは増えてよい）
        before = snapshot(s, with_obs=False); n_obs = len(s.obs)
        run_events(c["events"], s)
        res["gates"]["G3b_replay_idempotent"] = snapshot(s, with_obs=False) == before and len(s.obs) >= n_obs
    except Exception:
        res["error"] = traceback.format_exc()
    # 🤍20261115-7（KD-05）：G3b（同じ System への再投入で内容・予想・警告が増えない）を合否から外さない。
    # 旧版（v1）はここで G3b を gates から extra へ移していた。情報として extra にも同じ値を残す。
    res["extra"] = {"G3b_replay_idempotent": res.get("gates", {}).get("G3b_replay_idempotent")}
    res["ok"] = res["error"] is None and all(x["ok"] for x in res["checks"]) and all(res.get("gates", {}).values())
    return res


def main():
    cases_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    raw = cases_path.read_bytes()
    data = json.loads(raw)
    results = [run_case(c) for c in data["cases"]]
    summ = {}
    for grp in sorted({r["group"] for r in results}):
        rs = [r for r in results if r["group"] == grp]
        summ[grp] = {"total": len(rs), "passed": sum(r["ok"] for r in rs), "failed": sum(not r["ok"] for r in rs),
                     "not_run": 0, "errors": sum(r["error"] is not None for r in rs)}
    extra_ng = [r["id"] for r in results if r.get("extra", {}).get("G3b_replay_idempotent") is False]
    out = {"cases_sha256": hashlib.sha256(raw).hexdigest(), "summary": summ, "extra_G3b_replay_ng": extra_ng, "results": results}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    for r in results:
        bad = [x for x in r["checks"] if not x["ok"]]
        badg = [k for k, v in r.get("gates", {}).items() if not v]
        print("%-7s %-8s %s%s%s" % (r["id"], r["group"], "OK " if r["ok"] else "NG ",
                                    "" if not bad else " checks:" + json.dumps([(x["metric"], x["args"], x["expect"], x["got"]) for x in bad], ensure_ascii=False, default=str),
                                    ("" if not badg else " gates:" + ",".join(badg)) + ("" if not r["error"] else " ERROR " + r["error"].strip().splitlines()[-1])))
    print("EXTRA G3b_replay_ng", extra_ng)
    print("SUMMARY", json.dumps(summ, ensure_ascii=False))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
