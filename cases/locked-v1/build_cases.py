"""v0.3 の試験ケース（列挙78件）と安全①②③の追加試験を、実行前に機械可読（JSON）で書き出す。
この生成器は期待値の「原本」であり、実装コードを import しない。出力後は lock でハッシュを固定する。
時刻の書き方: "HH:MM[:SS]" は 2099-01-10、"MM-DD HH:MM" は 2099年、"YYYY-MM-DD HH:MM" は完全指定。JST。
"""
import json, sys
from pathlib import Path

def fresh(race, end, announce, values="V", **kw):
    e = {"op": "odds", "race": race, "stage": 1, "end": end, "announce": announce, "values": values}
    e.update(kw); return e

def confirm(race, tp, at, **kw):
    e = {"op": "confirm", "race": race, "tp": tp, "at": at}; e.update(kw); return e

def m(name, expect, **args):
    return {"metric": name, "args": args, "expect": expect}

C = []
def case(cid, group, design, events, checks, note=""):
    C.append({"id": cid, "group": group, "design": design, "events": events, "checks": checks, "note": note})

# ---------------- U: 未来情報 ----------------
case("U-01", "main", "v0.2", [
    fresh("T01", "09:40", "09:55", "X"),
    {"op": "view", "race": "T01", "tp": "15m"}],
    [m("view_count", 0, race="T01", tp="15m"), m("quarantine_count", 1)])
case("U-02", "main", "v0.2", [
    {"op": "odds", "race": "T01", "stage": 4, "end": "09:30", "announce": None, "values": "F"},
    confirm("T01", "15m", "09:45")],
    [m("final_store_count", 1), m("view_count", 0, race="T01", tp="15m"), m("pred_refs_final", 0, race="T01", tp="15m")])
case("U-03a", "main", "v0.2", [
    {"op": "row", "kind": "result", "race_date": "2099-01-17", "race_no": 2, "saved": "09:00"},
    {"op": "view", "race": "T02", "tp": "15m"}],
    [m("feature_rows", 0, race="T02", tp="15m", kind="result", race_date="2099-01-17"), m("quarantine_count", 1)])
case("U-03b", "main", "v0.2", [
    {"op": "row", "kind": "result", "race": "T02", "saved": "10:10"},
    {"op": "view", "race": "T02", "tp": "15m"}],
    [m("feature_rows", 0, race="T02", tp="15m", kind="result", race_date="2099-01-10"), m("quarantine_count", 1), m("warnings_total", 1)])
case("U-04", "main", "v0.2", [
    {"op": "row", "kind": "weight", "race": "T03", "announce": "10:05", "saved": "10:06"}],
    [m("feature_rows", 0, race="T03", tp="24h", kind="weight"), m("feature_rows", 0, race="T03", tp="1h", kind="weight"),
     m("feature_rows", 1, race="T03", tp="15m", kind="weight")],
    note="v0.2本文の24時間前時点『01-09 11:00』と1時間前『10:00』はT03（発走11:00）の時点そのもの。")
case("U-05", "main", "v0.2", [
    {"op": "odds", "race": "T03", "stage": None, "end": "10:00", "announce": None, "values": "A", "adapter": "accumulated"}],
    [m("view_count", 0, race="T03", tp="15m"), m("quarantine_count", 1)])

# ---------------- D: 遅延 ----------------
case("D-01a", "main", "v0.3改", [
    fresh("T04", "11:12", "11:11", "X"), fresh("T04", "11:16", "11:15", "Y"), confirm("T04", "15m", "11:15")],
    [m("pred_ref_values", "X", race="T04", tp="15m"), m("pred_ref_obs_end", "2099-01-10 11:12:00", race="T04", tp="15m"),
     m("race_state", "配分済み", race="T04", tp="15m"), m("warnings_total", 0)])
case("D-01b", "main", "v0.3読替", [
    fresh("T04", "11:05", "11:04", "X"), fresh("T04", "11:16", "11:15", "Y"), confirm("T04", "15m", "11:15")],
    [m("race_state", "未配分", race="T04", tp="15m"), m("reasons_include", True, race="T04", tp="15m", reason="鮮度切れ・取得"),
     m("amounts_all_empty", True, race="T04", tp="15m"), m("warnings_total", 1), m("pred_ref_count", 0, race="T04", tp="15m")],
    note="11:05の観測の発表時刻はv0.2に記載がないため11:04と置いた（取得の鮮度だけで不採用になる）。")
case("D-02", "main", "v0.3読替", [
    fresh("T04", "11:14", "11:13", "X"), confirm("T04", "15m", "11:31")],
    [m("eligibility_latest", "不適格", race="T04", tp="15m"), m("eligible_count", 0), m("ineligible_count", 1), m("pred_count", 1, race="T04")])
case("D-03a", "main", "v0.2", [
    fresh("T04", "11:14:55", "11:14", "X", skew=10), {"op": "view", "race": "T04", "tp": "15m"}],
    [m("warnings_total", 1), m("view_count", 0, race="T04", tp="15m")])
case("D-03b1", "main", "v0.3新", [
    {"op": "judge_clock", "independent": True, "trusted": True},
    fresh("T04", "11:12", "11:11", "X", skew=1), fresh("T04", "11:13", "11:12", "Y", skew=180), fresh("T04", "11:14", "11:13", "Z", skew=180),
    confirm("T04", "15m", "11:15")],
    [m("pred_ref_obs_end", "2099-01-10 11:12:00", race="T04", tp="15m"), m("quarantine_min", 1), m("race_state", "配分済み", race="T04", tp="15m"),
     m("halted", True)])
case("D-03b2", "main", "v0.3新", [
    {"op": "judge_clock", "independent": True, "trusted": True},
    fresh("T04", "11:05", "11:04", "X", skew=1), fresh("T04", "11:13", "11:12", "Y", skew=180), confirm("T04", "15m", "11:15")],
    [m("race_state", "未配分", race="T04", tp="15m"), m("reasons_include", True, race="T04", tp="15m", reason="時計ずれ"),
     m("pred_ref_count", 0, race="T04", tp="15m"), m("quarantine_min", 1)])
case("D-04a", "main", "v0.2", [
    fresh("T05", "01-09 11:59", "01-09 11:58", "V"), confirm("T05", "24h", "01-09 12:00"),
    {"op": "row", "kind": "post_change", "race": "T05", "announce": "10:50", "old": "12:00", "new": "12:20", "saved": "10:50"}],
    [m("schedule", "2099-01-10 11:20:00", race="T05", tp="1h"), m("schedule", "2099-01-10 12:05:00", race="T05", tp="15m"),
     m("pred_count", 1, race="T05"), m("pred_hashes_stable", True)])
case("D-04b", "main", "v0.3追記", [
    fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45"),
    {"op": "row", "kind": "post_change", "race": "T05", "announce": "11:50", "old": "12:00", "new": "12:30", "saved": "11:50"}],
    [m("pred_hashes_stable", True), m("pred_count", 1, race="T05"), m("warnings_total", 1),
     m("warning_field", "12:00→12:30", kind="post_change_after_final", field="change"),
     m("warning_field", "2099-01-10 11:50:00", kind="post_change_after_final", field="announce"),
     m("schedule", "2099-01-10 11:45:00", race="T05", tp="15m"),
     m("eligibility_latest", "適格", race="T05", tp="15m"), m("eligibility_rows", 2, race="T05", tp="15m")])

# ---------------- M: 欠損 ----------------
case("M-01", "main", "v0.3読替", [
    fresh("T05", "10:30", "10:29", "V"),
    fresh("T05", "10:40", None, None, result="failure"), fresh("T05", "10:47", None, None, result="failure"),
    fresh("T05", "10:53", None, None, result="failure"), fresh("T05", "11:00", None, None, result="failure"),
    confirm("T05", "1h", "11:00")],
    [m("obs_count", 4, race="T05", result="failure"), m("race_state", "未配分", race="T05", tp="1h"),
     m("reasons_include", True, race="T05", tp="1h", reason="取得失敗"), m("amounts_all_empty", True, race="T05", tp="1h"), m("warnings_total", 1)])
case("M-02a", "main", "v0.3改", [
    fresh("T05", "11:44", "11:43", "V", mods={"missing": 20, "missing_include": ["1-3-4"]}), confirm("T05", "15m", "11:45")],
    [m("bet_state", "未配分", race="T05", tp="15m", combo="1-3-4"), m("bet_reason", "欠損", race="T05", tp="15m", combo="1-3-4"),
     m("bet_state", "配分済み", race="T05", tp="15m", combo="1-2-3"), m("bet_state", "配分済み", race="T05", tp="15m", combo="1-2-4"),
     m("missing_rate", [20, 560], race="T05", tp="15m"), m("confidence_cap", None, race="T05", tp="15m")])
case("M-02b", "main", "v0.3明記", [
    fresh("T05", "11:44", "11:43", "V", mods={"missing": 60}), confirm("T05", "15m", "11:45")],
    [m("race_state", "配分済み", race="T05", tp="15m"), m("confidence_cap", "C", race="T05", tp="15m"),
     m("missing_rate", [60, 560], race="T05", tp="15m"), m("warnings_total", 1)])
case("M-02c", "main", "v0.3改", [
    fresh("T05", "11:44", "11:43", "V", mods={"missing": 140}), confirm("T05", "15m", "11:45")],
    [m("race_state", "未配分", race="T05", tp="15m"), m("reasons_include", True, race="T05", tp="15m", reason="欠損"),
     m("confidence", "Z", race="T05", tp="15m"), m("amounts_all_empty", True, race="T05", tp="15m")])
case("M-02d", "main", "v0.3新", [
    fresh("T05", "11:44", "11:43", "V", mods={"cancel_horse": 4}), confirm("T05", "15m", "11:45")],
    [m("missing_rate", [0, 455], race="T05", tp="15m"), m("bet_state", "取消", race="T05", tp="15m", combo="1-2-4"),
     m("bet_state", "取消", race="T05", tp="15m", combo="1-3-4"), m("bet_state", "配分済み", race="T05", tp="15m", combo="1-2-3"),
     m("confidence_cap", None, race="T05", tp="15m")])
case("M-02e", "main", "v0.3新", [
    fresh("T05", "11:44", "11:43", "V", mods={"novote": 50, "novote_include": ["1-3-4"]}), confirm("T05", "15m", "11:45")],
    [m("missing_rate", [0, 560], race="T05", tp="15m"), m("bet_state", "未配分", race="T05", tp="15m", combo="1-3-4"),
     m("bet_reason", "票なし", race="T05", tp="15m", combo="1-3-4"), m("confidence_cap", None, race="T05", tp="15m")])
case("M-02f", "main", "v0.3新", [
    fresh("T05", "11:44", "11:43", "V", mods={"invalid": 30}), confirm("T05", "15m", "11:45")],
    [m("missing_rate", [30, 560], race="T05", tp="15m"), m("confidence_cap", "C", race="T05", tp="15m"), m("parse_defects", 30)])
case("M-03", "main", "v0.2", [
    {"op": "row", "kind": "entries", "race": "T05", "field_size": 16, "horses": [h for h in range(1, 17) if h != 7], "saved": "01-09 18:00"},
    fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45")],
    [m("pred_count", 0, race="T05"), m("hold_count", 1, kind="entries_incomplete")])
case("M-04a", "main", "v0.2", [
    fresh("T05", "11:44", None, "V", announce_raw="00000000")],
    [m("content_count", 0), m("quarantine_count", 1), m("defect_count", 1)])
case("M-04b", "main", "v0.2", [
    {"op": "row", "kind": "race_detail", "race": "T05", "created": "2099-01-09", "saved": "01-09 19:10"}],
    [m("body_rows", 1, kind="race_detail"), m("defect_count", 0),
     m("feature_rows", 0, race="T05", at="01-09 19:09:59", kind="race_detail"), m("feature_rows", 1, race="T05", at="01-09 19:10", kind="race_detail")])
case("M-04c", "main", "v0.2", [
    {"op": "row", "kind": "race_detail", "race": "T05", "created": "2099-01-11", "saved": "09:00"}],
    [m("body_rows", 0, kind="race_detail"), m("quarantine_count", 1)])
case("M-04d", "main", "v0.2", [
    {"op": "odds", "race": "T05", "stage": 4, "end": "12:10", "announce": None, "values": "F"}],
    [m("final_store_count", 1), m("defect_count", 0), m("view_count", 0, race="T05", tp="15m")])

# ---------------- C: 取消 ----------------
SCR4 = {"race": "T06", "horse": 4, "kind": "出走取消", "announce": "09:00"}
case("C-01", "main", "v0.2", [
    {"op": "fetch_day", "date": "2099-01-10", "end": "09:05", "scratches": [SCR4]},
    fresh("T06", "11:29", "11:28", "V"), confirm("T06", "1h", "11:30")],
    [m("bets_with_horse", 0, race="T06", tp="1h", horse=4), m("bet_state", "取消", race="T06", tp="1h", combo="1-2-4"),
     m("bet_state", "取消", race="T06", tp="1h", combo="1-3-4"), m("bet_reason", "取消馬4", race="T06", tp="1h", combo="1-2-4")],
    note="『買い目』は状態が取消でないもの（配分済み・見送り・未配分）を数える。取消は外した記録として残す（v0.3 6章の状態表）。")
case("C-02", "main", "v0.2", [
    fresh("T06", "11:29", "11:28", "V"), confirm("T06", "1h", "11:30"),
    {"op": "fetch_day", "date": "2099-01-10", "end": "11:51", "scratches": [{"race": "T06", "horse": 1, "kind": "競走除外", "announce": "11:50"}]},
    fresh("T06", "12:14", "12:13", "V"), confirm("T06", "15m", "12:15")],
    [m("rebuild", True, race="T06", tp="15m"), m("rebuild_reason_present", True, race="T06", tp="15m"),
     m("bets_with_horse", 0, race="T06", tp="15m", horse=1)])
case("C-03a", "main", "v0.3新", [
    fresh("T06", "12:14", "12:13", "V"), confirm("T06", "15m", "12:15"),
    {"op": "fetch_day", "date": "2099-01-10", "end": "12:21", "scratches": [{"race": "T06", "horse": 1, "kind": "出走取消", "announce": "12:20"}]},
    {"op": "row", "kind": "payout", "race": "T06", "saved": "12:50", "refund_flag": True, "refund_horses": [1],
     "not_established": False, "special": False, "trio": {"combo": "2-3-5", "pay": 3000}}],
    [m("pred_hashes_stable", True), m("warnings_total", 1), m("settle_refund", 3000, race="T06"), m("settle_invest", 0, race="T06"),
     m("settle_basis", "返還フラグ・返還馬番", race="T06")])
case("C-03b", "main", "v0.3新", [
    fresh("T06", "12:14", "12:13", "V"), confirm("T06", "15m", "12:15"),
    {"op": "row", "kind": "payout", "race": "T06", "saved": "12:50", "refund_flag": False, "refund_horses": [],
     "not_established": True, "special": False, "trio": None}],
    [m("settle_refund", 3000, race="T06"), m("settle_invest", 0, race="T06"), m("settle_basis", "不成立フラグ", race="T06")])
case("C-03c", "main", "v0.3新", [
    fresh("T06", "12:14", "12:13", "V"), confirm("T06", "15m", "12:15"),
    {"op": "fetch_day", "date": "2099-01-10", "end": "12:21", "scratches": [{"race": "T06", "horse": 1, "kind": "出走取消", "announce": "12:20"}]},
    {"op": "row", "kind": "payout", "race": "T06", "saved": "12:50", "refund_flag": False, "refund_horses": [],
     "not_established": False, "special": False, "trio": {"combo": "2-3-5", "pay": 3000}}],
    [m("settle_rows", 0, race="T06"), m("hold_count", 1, kind="settle_mismatch"), m("warnings_kind", 1, kind="settle_mismatch")],
    note="軸馬の取消は最終確定後に届くため、取消の警告（C-03aの1件）も別に出る。期待値の『警告1件』は不一致の警告を指すので種類で数える。")
case("C-04", "main", "v0.2", [
    {"op": "fetch_day", "date": "2099-01-10", "end": "09:05", "scratches": [SCR4]},
    {"op": "fetch_day", "date": "2099-01-10", "end": "09:20", "scratches": [SCR4]}],
    [m("content_count", 1, kind="day"), m("obs_count", 2, kind="day"), m("warnings_total", 1)])
case("C-05", "main", "v0.3改", [
    fresh("T06", "01-09 12:29", "01-09 12:28", "V"), confirm("T06", "24h", "01-09 12:30"),
    {"op": "fetch_day", "date": "2099-01-10", "end": "10:00", "scratches": [SCR4]},
    {"op": "fetch_day", "date": "2099-01-10", "end": "10:15", "scratches": [], "result": "failure", "reached_end": False},
    {"op": "fetch_day", "date": "2099-01-10", "end": "10:30", "scratches": []},
    fresh("T06", "11:29", "11:28", "V"), confirm("T06", "1h", "11:30")],
    [m("withdrawal_count", 1), m("withdrawal_at", "2099-01-10 10:30:00"), m("scratch_rows", 1), m("pred_hashes_stable", True),
     m("bet_state", "配分済み", race="T06", tp="1h", combo="1-2-4")])
for wid, ev, why in [
    ("W-01", [{"op": "fetch_day", "date": "2099-01-10", "end": "10:30", "scratches": [], "complete": False}], "部分取得"),
    ("W-02", [{"op": "fetch_day", "date": "2099-01-10", "end": "10:30", "scratches": [], "result": "failure", "reached_end": False}], "途中切断"),
    ("W-03", [{"op": "fetch_day", "date": "2099-01-10", "end": "10:30", "scratches": [], "parse_errors": 1}], "解析不備"),
    ("W-04", [{"op": "fetch_day", "date": "2099-01-11", "end": "10:30", "scratches": []},
              {"op": "fetch_day", "date": "2099-01-10", "scope": "race", "race": "T03", "end": "10:31", "scratches": []}], "範囲違い")]:
    case(wid, "main", "v0.3新", [{"op": "fetch_day", "date": "2099-01-10", "end": "10:00", "scratches": [SCR4]}] + ev + [
        fresh("T06", "11:29", "11:28", "V"), confirm("T06", "1h", "11:30")],
        [m("withdrawal_count", 0), m("bets_with_horse", 0, race="T06", tp="1h", horse=4), m("undecidable_include", True, reason=why)])

# ---------------- R: 重複 ----------------
case("R-01", "main", "v0.3明記", [
    fresh("T04", "11:05", "11:04", "X"), fresh("T04", "11:10", "11:09", "X"), fresh("T04", "11:14", "11:13", "X"), confirm("T04", "15m", "11:15")],
    [m("content_count", 1, kind="odds"), m("obs_count", 3, race="T04"), m("pred_ref_obs_end", "2099-01-10 11:14:00", race="T04", tp="15m"),
     m("race_state", "配分済み", race="T04", tp="15m")])
case("R-02", "main", "v0.2", [
    fresh("T04", "11:10", "11:09", "X"), fresh("T04", "11:14", "11:13", "X", mods={"set": {"5-6-7": 99.9}})],
    [m("content_count", 2, kind="odds")])
case("R-03", "main", "v0.2", [
    fresh("T04", "11:10", "11:09", "X", result="interrupted"), fresh("T04", "11:12", "11:11", "X"),
    confirm("T04", "15m", "11:15"), confirm("T04", "15m", "11:15")],
    [m("content_count", 1, kind="odds"), m("pred_count", 1, race="T04"), m("obs_count", 2, race="T04")])
case("R-04", "main", "v0.2", [
    fresh("T04", "11:12", "11:11", "X"), confirm("T04", "15m", "11:15", unknown_result=True), confirm("T04", "15m", "11:15")],
    [m("pred_count", 1, race="T04")])
case("R-05", "main", "v0.3読替", [
    fresh("T04", "11:00", "11:00", "X"), fresh("T04", "11:08", None, None, result="failure"), fresh("T04", "11:13", None, None, result="failure"),
    confirm("T04", "15m", "11:15")],
    [m("race_state", "未配分", race="T04", tp="15m"), m("reasons_include", True, race="T04", tp="15m", reason="鮮度切れ・取得"),
     m("amounts_all_empty", True, race="T04", tp="15m"), m("warnings_total", 1)],
    note="11:00の観測の発表時刻はv0.2に記載がないため11:00と置いた。")

# ---------------- N: 未発売 ----------------
case("N-01", "main", "v0.3改", [
    {"op": "odds", "race": "T07", "stage": 1, "end": "01-09 12:59", "announce": None, "values": None, "result": "unpublished", "sale": "none"},
    confirm("T07", "24h", "01-09 13:00")],
    [m("obs_count", 1, race="T07", result="unpublished"), m("obs_count", 0, race="T07", result="failure"),
     m("race_state", "未配分", race="T07", tp="24h"), m("reasons_include", True, race="T07", tp="24h", reason="オッズ未発売"),
     m("amounts_all_empty", True, race="T07", tp="24h"), m("missing_rate", None, race="T07", tp="24h"), m("marks_present", True, race="T07", tp="24h")])
case("N-02", "main", "v0.2", [
    {"op": "odds", "race": "T07", "stage": 1, "end": "01-09 19:10", "announce": None, "values": None, "result": "unpublished", "sale": "none"},
    {"op": "odds", "race": "T07", "stage": 1, "end": "01-09 19:31", "announce": None, "values": None, "result": "unpublished", "sale": "none"}],
    [m("obs_count_min", 1, race="T07", result="failure"), m("warnings_total", 1)])

# ---------------- F: 最終確定後 ----------------
case("F-01", "main", "v0.2", [
    fresh("T08", "13:14", "13:13", "V", mods={"set": {"1-2-3": 40.0}}), confirm("T08", "15m", "13:15"),
    fresh("T08", "13:20", "13:19", "V", mods={"set": {"1-2-3": 12.0}}),
    {"op": "row", "kind": "track", "race": "T08", "announce": "13:20", "saved": "13:20", "value": "稍重", "old": "良"}],
    [m("pred_hashes_stable", True), m("warnings_total", 2), m("warnings_have_row_time", True)])
PAY = {"op": "row", "kind": "payout", "race": "T08", "saved": "14:00", "refund_flag": False, "refund_horses": [],
       "not_established": False, "special": False, "trio": {"combo": "1-2-3", "pay": 4000}}
case("F-02a", "main", "v0.2", [
    fresh("T08", "13:14", "13:13", "V"), confirm("T08", "15m", "13:15"), PAY,
    {"op": "row", "kind": "finish_correction", "race": "T08", "saved": "14:30"}],
    [m("settle_payout_latest", 40000, race="T08"), m("settle_rows", 1, race="T08"), m("note_count", 1, kind="着順訂正あり・払戻訂正なし"),
     m("pred_hashes_stable", True)])
case("F-02b", "main", "v0.2", [
    fresh("T08", "13:14", "13:13", "V"), confirm("T08", "15m", "13:15"), PAY,
    dict(PAY, saved="15:00", trio={"combo": "1-2-3", "pay": 5200}, correction=True)],
    [m("settle_rows", 2, race="T08"), m("settle_payout_latest", 52000, race="T08"), m("settle_payout_first", 40000, race="T08"),
     m("pred_hashes_stable", True)])

# ---------------- H: 過去再構成 ----------------
HIST = {"op": "hist_import", "today": "2099-02-01 09:00", "results": [
    {"race_date": "2099-01-03", "race_no": n} for n in range(1, 9)] + [
    {"race_date": "2099-01-10", "race_no": n} for n in range(1, 5)]}
case("H-01", "main", "v0.2", [HIST, {"op": "hist_predict", "race": "T04"}],
    [m("hist_feature_rows_min", 1, race="T04", race_date="2099-01-03"), m("hist_feature_basis_all", "再構成", race="T04")])
case("H-02", "main", "v0.2", [HIST, {"op": "hist_predict", "race": "T04"}],
    [m("hist_feature_rows", 0, race="T04", race_date="2099-01-10")])
case("H-03", "main", "v0.2", [
    dict(HIST, tamper={"race_date": "2099-01-03", "race_no": 9, "saved": "01-09 10:00"}),
    {"op": "hist_predict", "race": "T04", "want_odds": True}],
    [m("hist_odds_refs", 0, race="T04"), m("quarantine_count", 1)])
case("H-04", "main", "v0.2", [
    HIST, {"op": "hist_predict", "race": "T04"}, fresh("T04", "11:14", "11:13", "V"), confirm("T04", "15m", "11:15")],
    [m("agg_mixed", 0), m("hist_recovery", None), m("hist_note", "出走表は最終状態")])

# ---------------- P: 発表の鮮度 ----------------
case("P-01", "main", "v0.3新", [fresh("T04", "11:01", "11:00", "V1"), fresh("T04", "11:14", "11:00", "V1"), confirm("T04", "15m", "11:15")],
    [m("pred_ref_count", 0, race="T04", tp="15m"), m("race_state", "未配分", race="T04", tp="15m"),
     m("reasons_include", True, race="T04", tp="15m", reason="鮮度切れ・発表"), m("reasons_include", False, race="T04", tp="15m", reason="鮮度切れ・取得"),
     m("warnings_total", 1)])
case("P-02", "main", "v0.3新", [fresh("T04", "11:01", "11:00", "V1"), fresh("T04", "11:14", "11:12", "V1"), confirm("T04", "15m", "11:15")],
    [m("content_count", 1, kind="odds"), m("obs_count", 2, race="T04"), m("pred_ref_obs_end", "2099-01-10 11:14:00", race="T04", tp="15m"),
     m("pred_ref_announce", "2099-01-10 11:12:00", race="T04", tp="15m"), m("race_state", "配分済み", race="T04", tp="15m")])
case("P-03", "main", "v0.3新", [fresh("T04", "11:14", "11:05", "V"), confirm("T04", "15m", "11:15")],
    [m("race_state", "配分済み", race="T04", tp="15m")])
case("P-04", "main", "v0.3新", [fresh("T04", "11:14", "11:04", "V"), confirm("T04", "15m", "11:15")],
    [m("race_state", "未配分", race="T04", tp="15m"), m("reasons_include", True, race="T04", tp="15m", reason="鮮度切れ・発表")])
case("P-05", "main", "v0.3新", [fresh("T04", "11:10:00", "11:09", "V"), confirm("T04", "15m", "11:15")],
    [m("race_state", "配分済み", race="T04", tp="15m")])
case("P-06", "main", "v0.3新", [fresh("T04", "11:09:59", "11:09", "V"), confirm("T04", "15m", "11:15")],
    [m("race_state", "未配分", race="T04", tp="15m"), m("reasons_include", True, race="T04", tp="15m", reason="鮮度切れ・取得"),
     m("reasons_include", False, race="T04", tp="15m", reason="鮮度切れ・発表")])

# ---------------- Z: 前日売最終 ----------------
case("Z-01", "main", "v0.3新", [
    {"op": "odds", "race": "T07", "stage": 2, "end": "01-09 19:30", "announce": None, "values": "P"},
    confirm("T07", "24h", "01-09 13:00"), confirm("T07", "1h", "12:00"), confirm("T07", "15m", "12:45")],
    [m("defect_count", 0), m("prerelease_count", 1), m("view_count", 0, race="T07", tp="24h"), m("view_count", 0, race="T07", tp="1h"),
     m("view_count", 0, race="T07", tp="15m"), m("race_state", "未配分", race="T07", tp="15m"), m("pred_ref_count", 0, race="T07", tp="15m"),
     m("pred_ref_count", 0, race="T07", tp="1h")])

# ---------------- S: 適格性 ----------------
case("S-01", "main", "v0.3新", [
    confirm("T05", "15m", "11:55"),
    {"op": "row", "kind": "post_change", "race": "T05", "announce": "11:40", "old": "12:00", "new": "11:50", "saved": "12:10"}],
    [m("pred_hashes_stable", True), m("eligibility_rows", 2, race="T05", tp="15m"), m("eligibility_first", "適格", race="T05", tp="15m"),
     m("eligibility_latest", "不適格", race="T05", tp="15m"), m("eligible_count", 0)])
case("S-02", "main", "v0.3新", [
    fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45"),
    {"op": "row", "kind": "post_change", "race": "T05", "announce": "11:50", "old": "12:00", "new": "12:20", "saved": "11:50"}],
    [m("pred_count", 1, race="T05"), m("warnings_total", 1), m("eligibility_latest", "適格", race="T05", tp="15m"),
     m("eligibility_basis", "変更後", race="T05", tp="15m")])
case("S-03", "main", "v0.3新", [
    confirm("T05", "15m", "11:55"),
    {"op": "row", "kind": "post_change", "race": "T05", "announce": "11:40", "old": "12:00", "new": "12:10", "saved": "11:56"},
    {"op": "row", "kind": "post_change", "race": "T05", "announce": "11:40", "old": "12:00", "new": "11:50", "saved": "11:56:30"}],
    [m("eligibility_latest", "未確認", race="T05", tp="15m"), m("eligible_count", 0), m("unconfirmed_count", 1)])
case("S-04", "main", "v0.3新", [confirm("T05", "15m", "11:59:00")], [m("eligibility_latest", "適格", race="T05", tp="15m")])
case("S-05", "main", "v0.3新", [confirm("T05", "15m", "11:59:01")], [m("eligibility_latest", "未確認", race="T05", tp="15m")])

# ---------------- V: 見送り ----------------
case("V-01", "main", "v0.3新", [fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45", decision="none_meets")],
    [m("race_state", "見送り", race="T05", tp="15m"), m("amounts_all", 0, race="T05", tp="15m"), m("agg_skip_count", 1)])

# ---------------- B: 境界 ----------------
for bid, n, exp in [("B-01", 28, None), ("B-02", 29, "C"), ("B-03", 112, "C")]:
    ch = [m("confidence_cap", exp, race="T05", tp="15m"), m("missing_rate", [n, 560], race="T05", tp="15m")]
    if bid == "B-03": ch.append(m("race_state", "配分済み", race="T05", tp="15m"))
    case(bid, "main", "v0.3新", [fresh("T05", "11:44", "11:43", "V", mods={"missing": n}), confirm("T05", "15m", "11:45")], ch)
case("B-04", "main", "v0.3新", [fresh("T05", "11:44", "11:43", "V", mods={"missing": 113}), confirm("T05", "15m", "11:45")],
    [m("race_state", "未配分", race="T05", tp="15m"), m("reasons_include", True, race="T05", tp="15m", reason="欠損"), m("confidence", "Z", race="T05", tp="15m")])
case("B-05", "main", "v0.3新", [fresh("T04", "11:10", "11:09", "V", skew=2.0)], [m("warnings_total", 0), m("halted", False)])
case("B-06", "main", "v0.3新", [fresh("T04", "11:10", "11:09", "V", skew=2.1)],
    [m("warnings_total", 1), m("obs_avail_adjust_sec", 2.1, race="T04")])
case("B-07", "main", "v0.3新", [fresh("T04", "11:10", "11:09", "V", skew=30.0)], [m("halted", False), m("warnings_total", 1)])
case("B-08", "main", "v0.3新", [fresh("T04", "11:10", "11:09", "V", skew=30.1)], [m("halted", True), m("quarantine_min", 1)])

# ---------------- Y: 年またぎ ----------------
for yid, rdate, raw, exp in [
    ("Y-01", "2100-01-01", "12311900", "2099-12-31 19:00:00"), ("Y-02", "2099-12-30", "01011000", "2100-01-01 10:00:00"),
    ("Y-03", "2099-12-28", "01020900", None), ("Y-04", "2100-03-01", "02291200", None),
    ("Y-05", "2100-01-01", "12281900", None), ("Y-06", "2100-01-01", "12291900", "2099-12-29 19:00:00")]:
    ev = [{"op": "add_race", "race": "Y", "date": rdate, "post": "12:00"},
          {"op": "odds", "race": "Y", "stage": 1, "end": rdate + " 11:00", "announce": None, "announce_raw": raw, "values": "V"}]
    ch = ([m("content_count", 1), m("quarantine_count", 0), m("obs_announce", exp, race="Y")] if exp else
          [m("content_count", 0), m("quarantine_count", 1)])
    case(yid, "main", "v0.3新", ev, ch)

# ================= 安全①②③ 追加試験（別集計） =================
case("SA1-01", "safety1", "依頼 安全①", [fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45")],
    [m("eligibility_latest", "適格", race="T05", tp="15m"), m("eligibility_basis", "予定", race="T05", tp="15m"),
     m("provisional_count", 1), m("official_count", 0)], note="実発走が不明なら予定基準の暫定適格。正式成績に入れない。")
case("SA1-02", "safety1", "依頼 安全①", [fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45"),
    {"op": "row", "kind": "actual_post", "race": "T05", "actual": "12:01", "saved": "12:05"}],
    [m("eligibility_latest", "適格", race="T05", tp="15m"), m("eligibility_basis", "実際", race="T05", tp="15m"),
     m("official_count", 1), m("provisional_count", 0), m("pred_hashes_stable", True), m("eligibility_rows", 2, race="T05", tp="15m")])
case("SA1-03", "safety1", "依頼 安全①", [fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45"),
    {"op": "row", "kind": "actual_post", "race": "T05", "actual": "11:44", "saved": "12:05"}],
    [m("eligibility_latest", "不適格", race="T05", tp="15m"), m("official_count", 0), m("provisional_count", 0), m("ineligible_count", 1)])
case("SA1-04", "safety1", "依頼 安全①", [fresh("T05", "11:44", "11:43", "V"), confirm("T05", "15m", "11:45"),
    {"op": "row", "kind": "actual_post", "race": "T05", "actual": "12:03", "saved": "12:05"}],
    [m("pred_count", 1, race="T05"), m("pred_hashes_stable", True), m("warnings_kind", 1, kind="actual_post_after_final")],
    note="最終確定後の実発走情報は警告のみ。再予想しない。")
case("SA1-05", "safety1", "依頼 安全①", [confirm("T05", "15m", "11:59:00"),
    {"op": "row", "kind": "actual_post", "race": "T05", "actual": "12:00", "saved": "12:05"}],
    [m("eligibility_latest", "適格", race="T05", tp="15m"), m("official_count", 1)], note="実発走がちょうど60秒後でも適格（境界）。")
D3 = [fresh("T04", "11:12", "11:11", "X", skew=1), fresh("T04", "11:13", "11:12", "Y", skew=180), confirm("T04", "15m", "11:15")]
case("SA2-01", "safety2", "依頼 安全②", [{"op": "judge_clock", "independent": False, "trusted": True}] + D3,
    [m("pred_count", 0, race="T04"), m("hold_count", 1, kind="judge_clock_uncertain")], note="判断時計が取得機と同じ時計なら確定保留。")
case("SA2-02", "safety2", "依頼 安全②", [{"op": "judge_clock", "independent": True, "trusted": False}] + D3,
    [m("pred_count", 0, race="T04"), m("hold_count", 1, kind="judge_clock_uncertain")], note="判断時計が独立でも信頼できなければ確定保留。")
case("SA2-03", "safety2", "依頼 安全②", [{"op": "judge_clock", "independent": True, "trusted": True}] + D3,
    [m("pred_count", 1, race="T04"), m("race_state", "配分済み", race="T04", tp="15m"), m("hold_count", 0, kind="judge_clock_uncertain")])
case("SA2-04", "safety2", "依頼 安全②", [{"op": "judge_clock", "independent": True, "trusted": False},
    fresh("T04", "11:14", "11:13", "X"), confirm("T04", "15m", "11:15")],
    [m("pred_count", 0, race="T04"), m("hold_count", 1, kind="judge_clock_uncertain")], note="取得側にずれがなくても、判断時計が不確実なら確定しない。")
case("SA3-01", "safety3", "依頼 安全③", [fresh("T05", "11:14", "11:13", "S"), fresh("T04", "11:00", "10:59", "S"), confirm("T04", "15m", "11:15")],
    [m("content_count", 1, kind="odds"), m("pred_ref_count", 0, race="T04", tp="15m"), m("race_state", "未配分", race="T04", tp="15m")],
    note="T05の新しい観測とT04の古い観測が同じ内容を共有しても、T04の判断にT05の観測を使わない。")
case("SA3-02", "safety3", "依頼 安全③", [fresh("T04", "11:00", "10:59", "S"),
    {"op": "odds", "race": "T04", "stage": 3, "end": "11:14", "announce": None, "values": "S"}, confirm("T04", "15m", "11:15")],
    [m("content_count", 1, kind="odds"), m("final_store_count", 1), m("pred_ref_count", 0, race="T04", tp="15m"),
     m("pred_refs_final", 0, race="T04", tp="15m"), m("race_state", "未配分", race="T04", tp="15m")],
    note="最終オッズ（区分3）と同じ内容でも、最終の観測を判断に使わない。")
case("SA3-03", "safety3", "依頼 安全③", [fresh("T04", "11:14", "11:13", "S", target=None), confirm("T04", "15m", "11:15")],
    [m("quarantine_count", 1), m("content_count", 0), m("pred_ref_count", 0, race="T04", tp="15m")])
case("SA3-04", "safety3", "依頼 安全③", [fresh("T04", "11:14", "11:13", "S", stage=None)],
    [m("quarantine_count", 1), m("content_count", 0)])
case("SA3-05", "safety3", "依頼 安全③", [fresh("T04", "11:14", None, "S")],
    [m("quarantine_count", 1), m("content_count", 0)], note="中間オッズで発表時刻が空。")
case("SA3-06", "safety3", "依頼 安全③", [fresh("T04", "11:14", "11:13", "S", obs_id=None)],
    [m("quarantine_count", 1), m("content_count", 0)], note="観測IDなし。")
case("SA3-07", "safety3", "依頼 安全③", [fresh("T04", "11:14", "11:13", "S"), confirm("T04", "15m", "11:15")],
    [m("pred_ref_fields_complete", True, race="T04", tp="15m")], note="参照に対象キー・段階・発表時刻・観測IDがすべて残る。")

if __name__ == "__main__":
    out = Path(sys.argv[1])
    main = [c for c in C if c["group"] == "main"]
    ids = [c["id"] for c in C]
    assert len(ids) == len(set(ids)), "ID重複"
    data = {"format": "keiba-proto-cases/1", "design": "v0.3 https://app.notion.com/p/418f1dff94dc81328a16f7b4316b7330",
            "count_main": len(main), "count_safety": len(C) - len(main), "cases": C}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    print("main", len(main), "safety", len(C) - len(main))
