"""💚20261128-2（みにの受け入れ再検証）の指摘。収集はふつうの見回り（cli の collect tick）から通す。"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from keiba import official, review
from keiba.app.data import Source
from keiba.ledger import Ledger
from keiba.live import wallet
from keiba.live.collector import ARCHIVE_FIELDS, ARCHIVE_FILE, Collector
from tests.test_capture_window_shift import DAY, RK, T
from tests.test_entry_point_20261125 import _mam_1h, _replay_trio_rows


def _recs(root):
    return [json.loads(x) for x in (root / "snapshots.jsonl").read_text(encoding="utf-8").splitlines()]


# ---- 止める1：締切の代わりの予想が、前の回の三連複と今の回の出馬表を混ぜる ------------------------------------

def test_fallback_after_stop_uses_only_the_latest_round(tmp_path):
    """14:25の見回りが完全な組（出馬表・単勝・三連複）を保存した直後、取得済みの前に止まった。14:30・14:35は出馬表と単勝は
    取れるが三連複のタブが無い。締切の後の代わりの予想は、いちばん新しい回（14:35）の原本だけで作り、14:25の三連複と
    混ぜない。その回に三連複が無いので、オッズの判定は不合格でお金は配分しない。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "fb", "trio_tab_gone_from_1430")
    tick("14:00")
    with patch.object(official, "material_time", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        tick("14:25")
    tick("14:30")
    tick("14:35")
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    last = tick("14:41")
    assert last["missed"] >= 1 and any(f"{RK}|1h" in m for m in told)
    recs = [x for x in _recs(root) if x["time_point"] == "1h"]
    latest = [x for x in recs if x["fetched_at"].startswith(f"{DAY}T14:35")]
    assert {x["kind"] for x in latest} >= {"card", "odds_win"} and "odds_trio" not in {x["kind"] for x in latest}
    # 予想に使った原本は、14:35の回のものだけ
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    _, _, inputs = wallet.live_tables(root, state, wallet._snapshots(root))
    used = set(inputs[(RK, "1h")]["snapshots"])
    assert used <= {x["sha256"] for x in latest}
    assert not inputs[(RK, "1h")]["gate"]["pass"]
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    assert preds
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes") and not v.get("total"), (p["method"], a, v)
    p = Source(paths, "live").preds[RK][("ml_lgbm", "1h")]
    assert not p["odds_gate"]["pass"]
    assert _mam_1h(paths) is None


def test_trio_back_in_the_last_round_is_taken_normally(tmp_path):
    """対照：14:25の完全な組の直後に止まり、14:30は三連複のタブが無い。14:35に戻れば、その回のひと組で取得済み・正式の
    予想・まむの入力を作り、ふつうに配分する（原本は全部14:35の回）。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "back", "trio_tab_gone_at_1430")
    tick("14:00")
    with patch.object(official, "material_time", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        tick("14:25")
    tick("14:30")
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    tick("14:35")
    assert f"{RK}|1h" in Collector(root, client=client).state["taken"]
    latest = [x for x in _recs(root) if x["time_point"] == "1h" and x["fetched_at"].startswith(f"{DAY}T14:35")]
    assert {x["kind"] for x in latest} >= {"card", "odds_win", "odds_trio"}
    assert len({x["capture_round"] for x in latest}) == 1
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    _, _, inputs = wallet.live_tables(root, state, wallet._snapshots(root))
    assert set(inputs[(RK, "1h")]["snapshots"]) <= {x["sha256"] for x in latest}
    p = Source(paths, "live").preds[RK][("ml_lgbm", "1h")]
    assert p["odds_gate"]["pass"]
    assert _mam_1h(paths) is not None
    assert tick("14:41")["missed"] == 0


def test_odds_only_round_after_stop_does_not_pair_with_the_earlier_card(tmp_path):
    """止める1の別の形：14:25の完全な組の直後に止まり、14:30からは一覧の出馬表リンクが無く、オッズだけ取れる回が続く。
    締切の代わりの予想は前の回（14:25、時間帯の中）の出馬表で作るが、回の違う原本の組なので、お金は配分せず、
    まむへの入力も出さない。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "oo", "card_link_gone_from_1430")
    tick("14:00")
    with patch.object(official, "material_time", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        tick("14:25")
    for hm in ("14:30", "14:35"):
        tick(hm)
    assert f"{RK}|1h" not in Collector(root, client=client).state["taken"]
    tick("14:41")
    later = [x for x in _recs(root) if x["time_point"] == "1h" and x["fetched_at"] >= f"{DAY}T14:30"]
    assert later and "card" not in {x["kind"] for x in later}
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    _, _, inputs = wallet.live_tables(root, state, wallet._snapshots(root))
    inp = inputs[(RK, "1h")]
    assert inp["roster_issue"] and not inp["gate"]["pass"] and not inp["win"]["ok"]
    led = Ledger(paths.ledger_path, read_only=True)
    preds = [p for p in led.predictions() if p["race_key"] == RK and p["time_point"] == "1h"]
    led.close()
    assert preds
    for p in preds:
        for a, v in (p.get("allocation") or {}).items():
            if isinstance(v, dict):
                assert not v.get("stakes") and not v.get("total"), (p["method"], a, v)
    assert _mam_1h(paths) is None


# ---- 止める2：保存した後の取り直しで、保存ずみの正式な予想が外れる ------------------------------------------

def _utc(hm):
    return T(hm).astimezone(timezone.utc).isoformat(timespec="seconds")


@pytest.mark.parametrize("later", ["partial", "complete", "none"])
def test_saved_official_prediction_stays_after_post_change_and_recapture(later):
    """14:25に、そろった回（r1）で1時間前の正式な予想を保存した。14:30の一覧で発走15:40→15:45になり、新しい時間帯で
    取り直した（r2：途中／完全／取り直し無し）。締切は保存した時点の発走15:40で決まり（14:40）、r2も前の時間帯の中に入る。
    判定は保存した時点までに取った記録だけで見るので、保存ずみの予想は正式のまま（後の取り直しで外さない）。"""
    rows = [(RK, "1h", "ml_lgbm", "live-x", _utc("14:25"))]
    recs = [("card", T("14:25").isoformat(), "r1", False), ("odds_trio", T("14:25").isoformat(), "r1", False)]
    if later in ("partial", "complete"):
        recs.append(("card", T("14:30").isoformat(), "r2", False))
    if later == "complete":
        recs.append(("odds_trio", T("14:30").isoformat(), "r2", False))
    history = {RK: [(T("09:00"), "15:40"), (T("14:30"), "15:45")]}
    sel = official.select(rows, {RK: "15:45"}, {(RK, "1h"): recs}, history)
    key = (RK, "1h", "ml_lgbm", "live-x")
    assert key in sel.valid and key not in sel.early and key not in sel.fallback
    assert sel.counted[(RK, "ml_lgbm")] == ("1h", "live-x")


def test_prediction_saved_before_material_is_still_early():
    """対照：材料がそろう前（14:20。その時点では出馬表だけ）に保存した予想は、今までどおり数えない。後で（14:25）そろった
    回の予想を数える。"""
    rows = [(RK, "1h", "ml_lgbm", "live-a", _utc("14:20")), (RK, "1h", "ml_lgbm", "live-b", _utc("14:25"))]
    recs = [("card", T("14:20").isoformat(), "r1", False),
            ("card", T("14:25").isoformat(), "r2", False), ("odds_trio", T("14:25").isoformat(), "r2", False)]
    sel = official.select(rows, {RK: "15:40"}, {(RK, "1h"): recs}, {RK: [(T("09:00"), "15:40")]})
    assert (RK, "1h", "ml_lgbm", "live-a") in sel.early
    assert sel.counted[(RK, "ml_lgbm")] == ("1h", "live-b")


def test_capture_in_the_same_second_as_saving_counts():
    """台帳の時刻は秒まで（切り捨て）。同じ秒の中で取得（14:25:00.6）してから保存（14:25:00）した予想も、その取得を見ている。"""
    rows = [(RK, "1h", "ml_lgbm", "live-x", _utc("14:25"))]
    t = (T("14:25") + timedelta(milliseconds=600)).isoformat()
    recs = [("card", t, "r1", False), ("odds_trio", t, "r1", False)]
    sel = official.select(rows, {RK: "15:40"}, {(RK, "1h"): recs}, {RK: [(T("09:00"), "15:40")]})
    assert (RK, "1h", "ml_lgbm", "live-x") in sel.valid and (RK, "1h", "ml_lgbm", "live-x") not in sel.fallback


# ---- 止める3：予定表のふつうの整理で、保存した月次のオッズの判定が消える -------------------------------------

def _archive(root):
    """収集の整理と同じ形で、レースを予定表から外して races_archive.jsonl に残す（取得の対象には戻さない）。"""
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    r = state["races"].pop(RK)
    with open(root / ARCHIVE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"race_key": RK, **{k: r.get(k) for k in ARCHIVE_FIELDS},
                            "archived_at": "2026-11-30T05:00:00+09:00"}, ensure_ascii=False) + "\n")
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_review_keeps_odds_checks_of_archived_races(tmp_path):
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "arc", "display_row")
    for hm in ("14:00", "14:25", "14:41"):
        tick(hm)
    month = DAY[:7]
    clock = T("14:41") + timedelta(days=8)
    with patch.object(wallet, "prediction_clock", lambda: clock):
        before = review.build_review(paths, month)["odds_gate"]
        assert before and {x["time_point"] for x in before} >= {"1h"}
        review.write_review(paths, month)
        _archive(root)
        assert RK not in json.loads((root / "state.json").read_text(encoding="utf-8"))["races"]
        after = review.build_review(paths, month)["odds_gate"]
        assert after == before
        assert month in review.stale_months(paths)            # 整理で読むものが変わった：書き直す
        review.write_review(paths, month)
        saved = json.loads((paths.data_dir / "review" / f"{month}.json").read_text(encoding="utf-8"))
        assert saved["odds_gate"] == json.loads(json.dumps(before, default=str))
    # 外したレースを予定表（取得の対象）には戻さない
    assert RK not in json.loads((root / "state.json").read_text(encoding="utf-8"))["races"]


def test_review_written_by_the_previous_format_is_rewritten(tmp_path):
    """前の作り方（目印に作り方の版が無い）で書いた月次は、読むものが何も変わらなくても次の見回りで書き直す
    （前の作り方で落としたオッズの判定を取り戻す）。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "fmt", "display_row")
    tick("14:00")
    tick("14:25")
    month = DAY[:7]
    review.write_review(paths, month)
    assert month not in review.stale_months(paths)
    f = paths.data_dir / "review" / review.MARKS_FILE
    marks = json.loads(f.read_text(encoding="utf-8"))
    marks[month] = marks[month].split(";", 1)[1]                # 版の無い前の形の目印
    f.write_text(json.dumps(marks), encoding="utf-8")
    assert month in review.stale_months(paths)


# ---- あとで：同じ秒の2つの取得の回 -------------------------------------------------------------------------

@pytest.mark.parametrize("first, second", [("ffffffffffff", "111111111111"), ("111111111111", "ffffffffffff")])
def test_rounds_in_the_same_second_follow_the_index_order(first, second):
    """同じ秒に2回取った：後から目録に書いた回を新しいとする（取得の回の印の文字の順では決めない）。後の回が途中なら
    そろっていない（前の回の完全な組を使わない）。"""
    t = T("14:25").isoformat()
    recs = [("card", t, first, False), ("odds_trio", t, first, False),
            ("card", t, second, False), ("odds_trio", t, second, True)]
    assert official.material(recs, T("14:40")) == (None, second)
    recs[-1] = ("odds_trio", t, second, False)
    assert official.material(recs, T("14:40")) == (T("14:25"), second)


def test_two_rounds_in_the_same_second_keep_both_originals(tmp_path):
    """同じ秒に2回取っても、原本のファイル名に取得の回の印が入るので、前の回の原本を上書きしない（目録のハッシュと一致）。"""
    import hashlib
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "ss", "display_row")
    tick("14:00")
    col = Collector(root, client=client, now=lambda: T("14:25"))
    col.capture(RK, "1h")
    col2 = Collector(root, client=client, now=lambda: T("14:25"))
    col2.state["taken"].pop(f"{RK}|1h", None)
    col2.capture(RK, "1h")
    cards = [x for x in _recs(root) if x["kind"] == "card" and x["time_point"] == "1h"]
    assert len(cards) == 2 and len({x["path"] for x in cards}) == 2
    for x in _recs(root):
        if x.get("path"):
            assert hashlib.sha256((root / x["path"]).read_bytes()).hexdigest() == x["sha256"]


# ---- 💚20261128-4 止める1：ほかの時点の出馬表・途中の印の三連複でお金を配分する ------------------------------

def _replay_post_delay(directory, control):
    """15:40発走の15:10に15分前の出馬表を取る（三連複のタブは無い）。15:15の一覧で発走が16:20に延び、新しい1時間前の
    時間帯（15:05〜15:20）になる。control でなければ一覧の出馬表リンクが欠け、単勝・三連複だけ取れる（三連複は同じ回の
    名簿が無いので途中の印）。"""
    from contextlib import redirect_stdout
    from io import StringIO
    from types import SimpleNamespace
    from keiba import cli
    from keiba.live import collector as collector_module
    from tests.test_capture_window_shift import CARD, LIST, _pages, _paths
    from tests.test_live_collector import FakeClient, INDEX, race_list
    from tests.test_live_wallet import result_html, trio_html, win_html
    paths = _paths(directory)
    clock = {"now": T("14:45")}
    told = []

    def fetch(name):
        now = clock["now"]
        if name.startswith("pw01dli"):
            return INDEX
        delayed = now >= T("15:15")
        pages = _pages({"now": T("14:25")})
        if name == LIST:
            listing = race_list(odds=True, result=now >= T("16:50")).replace("18頭", "8頭")
            if delayed:
                listing = listing.replace("15時40分", "16時20分")
                if not control:
                    listing = listing.replace(f"?CNAME={CARD}", "")
            return listing
        if name == "pw15orl10/AA" and not delayed:
            return pages[name].splitlines()[0]
        if name == "pw151ou1/11":
            return win_html(time=f"{now:%H}時{now:%M}分")
        if name == "pw156ou1/66":
            return trio_html(time=f"{now:%H}時{now:%M}分")
        if name == "pw01sde1008202605061120261122/BD":
            return result_html([1, 2, 3, 4, 5, 6, 7, 8])
        return pages[name]
    client = FakeClient(fetch)

    def tick(hm):
        clock["now"] = T(hm)
        out = StringIO()
        with patch.object(collector_module, "Collector",
                          lambda folder: Collector(folder, client=client, now=lambda: clock["now"])), \
                patch.object(Collector, "check_notices", lambda self, log=print: None), \
                patch.object(cli, "_notify", lambda *a: told.append(a)), \
                patch.object(wallet, "prediction_clock", lambda: clock["now"]), \
                patch.object(Ledger, "_now", lambda _: clock["now"].astimezone(timezone.utc).isoformat(timespec="seconds")), \
                redirect_stdout(out):
            cli.cmd_collect(paths, SimpleNamespace(action="tick"))
        return json.loads(out.getvalue().splitlines()[-1])
    return paths, tick


def _ev_settlements(paths):
    led = Ledger(paths.ledger_path, read_only=True)
    try:
        return [(s["time_point"], s["status"], s["stake"]) for s in led.settlements()
                if s["race_key"] == RK and s["method"] == "ml_lgbm" and s["wallet"] == "ev_weighted"]
    finally:
        led.close()


@pytest.mark.parametrize("control", [False, True])
def test_post_delay_does_not_pair_another_time_points_card_with_new_odds(tmp_path, control):
    paths, tick = _replay_post_delay(tmp_path / ("ctl" if control else "mix"), control)
    for hm in ("14:45", "15:10", "15:15", "15:20"):
        tick(hm)
    p = Source(paths, "live").preds[RK][("ml_lgbm", "1h")]
    assert p["odds_gate"]["pass"] is control
    assert bool(p["allocation"]["ev_weighted"]["total"]) is control
    assert (_mam_1h(paths) is not None) is control
    tick("16:50")
    st = _ev_settlements(paths)
    if control:
        assert st and st[0][1] == "settled" and st[0][2] > 0
    else:
        assert st == [("1h", "not_allocated_gate", 0)]
        reasons = " ".join(p["odds_gate"]["reasons"])
        assert "途中と記録した三連複" in reasons and "出馬表が無い" in reasons


def test_incomplete_trio_stays_rejected_after_the_list_count_changes(tmp_path):
    """14:25に一覧8頭・出馬表7頭で、三連複を途中と記録した。後で一覧の頭数だけ7頭に直り、新しい原本は取れない。
    取得のときの途中の印は外さず、オッズの判定は不合格（お金は配分しない）。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "cnt", "card_short_always")
    tick("14:00")
    tick("14:25")
    recs = [x for x in _recs(root) if x["time_point"] == "1h" and x["kind"] == "odds_trio"]
    assert recs and recs[-1].get("incomplete")
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    state["races"][RK]["field_size"] = 7                     # 一覧の頭数だけ直った
    _, _, inputs = wallet.live_tables(root, state, wallet._snapshots(root))
    g = inputs[(RK, "1h")]["gate"]
    assert not g["pass"] and any("途中と記録した三連複" in r for r in g["reasons"])


# ---- 💚20261128-4 止める2：原本を読めない回に月次を上書きする ------------------------------------------------

def test_review_is_not_written_while_an_original_is_unreadable(tmp_path):
    """結果まで取った月の月次を書いた後、その月のレースの原本（結果のページ）が一時的に読めない。月次は書かずに
    （JSON・HTML・目印はそのまま）失敗として次の回に残し、原本が戻った回に書き直す。"""
    paths, root, client, tick, told = _replay_trio_rows(tmp_path / "unr", "display_row")
    for hm in ("14:00", "14:25", "14:41"):
        tick(hm)
    month = DAY[:7]
    review.write_review(paths, month)
    out = paths.data_dir / "review"
    files = {n: (out / n).read_bytes() for n in (f"{month}.json", f"{month}.html", review.MARKS_FILE)}
    card = next(x for x in reversed(_recs(root)) if x["kind"] == "card")
    src = root / card["path"]
    hold = tmp_path / "hold.html"
    src.rename(hold)
    try:
        with pytest.raises(review.ReviewUnreadable):
            review.build_review(paths, month)
        marks = json.loads((out / review.MARKS_FILE).read_text(encoding="utf-8"))
        marks[month] = "v=1;old"                               # 書き直しが必要な月
        (out / review.MARKS_FILE).write_text(json.dumps(marks), encoding="utf-8")
        files[review.MARKS_FILE] = (out / review.MARKS_FILE).read_bytes()
        res = tick("14:46")
        assert any("ReviewUnreadable" in x for x in res.get("review_error", [])), res
        assert {n: (out / n).read_bytes() for n in files} == files   # 1バイトも変えない
        assert month in review.stale_months(paths)
    finally:
        hold.rename(src)
    tick("14:51")                                               # 原本が戻った回に書き直す
    assert month not in review.stale_months(paths)
    saved = json.loads((out / f"{month}.json").read_text(encoding="utf-8"))
    assert saved["races_predicted"] == 1 and saved["odds_gate"]
