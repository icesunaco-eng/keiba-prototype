"""見回りがまむへの入力を置くときの締切と、読むページ（みに💚20261126-1 の指摘。PR #18）。

- 要修正：書き出しを締切前に始めても、正式な名前に置く前に締切を過ぎたら置かない（置く直前に時計を見直す）
- 提案：置けていない時点を数える側（unplaced）と置く側（export_packets）が同じ条件（_material）を使い、
  置く時点の入力に使わないページ（締切を過ぎた24時間前のオッズなど）は読まない。数えるのにかかった時間も残す
"""
import errno
import io
import json
import time
from contextlib import redirect_stdout
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from keiba import cli
from keiba.live import collector, mam, wallet
from tests.test_live_wallet import card_html, trio_html, win_html
from tests.test_mam_resume import AT, RK, TP, _client, _no_links, _paths, _placed_once

R12 = "2025-08-30|京都|12"
REAL_COLLECTOR = collector.Collector   # 見回りの中から別の見回りを動かすとき、差し替え中の代役ではなく本物を使う
DL11 = AT.replace(minute=40)   # 11R の1時間前の締切（12R は 14:48）
IDS = [f"{n:010}" for n in range(8)]


def _tick(paths, clock, **client):
    """ふだんの入口（cli の collect tick）を、合成のページと動かせる時計（clock[0]）で1回動かす。予想・精算は止めておく。"""
    real = collector.Collector
    buf = io.StringIO()
    with patch.object(collector, "Collector", side_effect=lambda root: real(root, client=_client(**client), now=lambda: clock[0])), \
         patch.object(wallet, "run_live", return_value={"settled": 0}), patch.object(cli, "_notify"), redirect_stdout(buf):
        cli.cmd_collect(paths, SimpleNamespace(action="tick"))
    return json.loads(buf.getvalue().splitlines()[-1])


def _no_leftovers(paths):
    """書きかけ（outbox-staging・outbox の隠し名）が残っていない。"""
    stage = paths.data_dir / "mam" / "outbox-staging"
    assert not stage.exists() or not list(stage.iterdir())
    assert not [p for p in (paths.data_dir / "mam" / "outbox").iterdir() if p.name.startswith(".")]


def _unplaced_pages(paths, clock, fresh: bool):
    """11R（14:30）と、静かな回なら12R（14:35）のページを保存したが、入力は置けずに終わった状態を作る。"""
    def eio(*_a, **_k):
        raise OSError(errno.EIO, "置き場が一時的に使えない")
    with patch.object(mam, "_publish_once", eio):
        assert _tick(paths, clock, another=True)["saved"] == 3
        if not fresh:
            clock[0] = AT.replace(minute=35)
            assert _tick(paths, clock, another=True)["saved"] == 3
    assert not mam._packet_path(paths, RK, TP).exists() and not mam._packet_path(paths, R12, TP).exists()


@pytest.mark.parametrize("fresh", [False, True], ids=["quiet-tick", "tick-with-new-pages"])
def test_deadline_passing_while_writing_is_not_placed(tmp_path, fresh):
    """14:39:59 に始めた見回りが、11R の入力を書き終えた所で締切 14:40 をまたいでも、11R は置かず、締切前の12R だけ置く。
    新しいページの無い回（置き直し）と、12R のページを取った回（ふつうの書き出し）の両方。"""
    paths = _paths(tmp_path)
    clock = [AT]
    _unplaced_pages(paths, clock, fresh)
    clock[0] = DL11 - timedelta(seconds=1)
    real = mam._write_complete
    placed_at = {}
    real_link = mam.os.link

    def write(path, text):
        real(path, text)
        if json.loads(text)["race_key"] == RK:   # 11R を書いて同期し終えた所で、時計が締切を越える
            clock[0] = DL11 + timedelta(seconds=1)

    def link(src, dst, *a, **k):
        real_link(src, dst, *a, **k)
        placed_at[dst.name] = clock[0]

    with patch.object(mam, "_write_complete", write), patch.object(mam.os, "link", link):
        out = _tick(paths, clock, another=True)
    assert out["saved"] == (3 if fresh else 0)
    assert out["mam_packets"] == 1
    assert not mam._packet_path(paths, RK, TP).exists()                  # 締切後の入力は正式な名前に置かない
    assert mam.issued_roster(paths, R12, TP) == list(range(1, 9))       # 締切前の12R は置く
    assert list(placed_at) == [mam._packet_path(paths, R12, TP).name]
    assert all(t < DL11 + timedelta(minutes=8) for t in placed_at.values())
    assert (RK, TP) not in mam.unplaced(paths, clock[0])
    _no_leftovers(paths)


@pytest.mark.parametrize("cross", ["outside-outbox", "hidden-name"])
def test_deadline_is_checked_again_before_each_way_of_placing(tmp_path, cross):
    """ハードリンクの使えない置き場（隠し名からの上書きしない移動）でも、置く直前ごとに締切を見直す。"""
    paths = _paths(tmp_path)
    clock = [AT]
    _unplaced_pages(paths, clock, fresh=True)
    clock[0] = DL11 - timedelta(seconds=1)
    real = mam._write_complete
    renamed = []
    writes = []

    def write(path, text):
        real(path, text)
        if json.loads(text)["race_key"] == RK:
            writes.append(path)
            if (cross == "outside-outbox") == (len(writes) == 1):
                clock[0] = DL11 + timedelta(seconds=1)

    real_rename = mam._rename_noreplace

    def rename(src, dst):
        renamed.append(dst.name)
        return real_rename(src, dst)

    with patch.object(mam, "_write_complete", write), patch.object(mam.os, "link", _no_links), \
         patch.object(mam, "_rename_noreplace", rename):
        out = _tick(paths, clock, another=True)
    assert out["mam_packets"] == 1
    assert not mam._packet_path(paths, RK, TP).exists()
    assert renamed == [mam._packet_path(paths, R12, TP).name]           # 11R は移す操作まで進まない
    assert mam.issued_roster(paths, R12, TP) == list(range(1, 9))
    assert len(writes) == (1 if cross == "outside-outbox" else 2)
    _no_leftovers(paths)


def test_publish_once_does_not_place_after_the_cutoff(tmp_path):
    """_publish_once の直接の確かめ：締切を過ぎた時計なら置かず、書きかけも残さない。締切前なら置く。"""
    paths = _paths(tmp_path)
    text = json.dumps({"race_key": RK, "time_point": TP, "horses": [{"horse_number": n} for n in range(1, 9)]})
    (paths.data_dir / "mam" / "outbox").mkdir(parents=True)
    assert mam._publish_once(paths, RK, TP, text, cutoff=DL11, clock=lambda: DL11) is None
    assert not mam._packet_path(paths, RK, TP).exists()
    _no_leftovers(paths)
    assert mam._publish_once(paths, RK, TP, text, cutoff=DL11, clock=lambda: DL11 - timedelta(seconds=1)) is not None
    assert mam.issued_roster(paths, RK, TP) == list(range(1, 9))


def _seed_24h_then_1h(paths):
    """前日に24時間前の出馬表・単勝のページ、当日 14:30 に1時間前の出馬表のページを保存した状態（入力はまだ無い）。"""
    live = paths.data_dir / "live"
    at = [AT - timedelta(hours=23)]
    seed = collector.Collector(live, client=_client(), now=lambda: at[0])
    seed.state["races"][RK] = {"race_number": 11, "post_time": "15:40", "race_date": "2025-08-30", "venue": "京都",
                               "field_size": 8, "known_at": at[0].isoformat()}
    seed.state["taken"] = {f"{RK}|24h": at[0].isoformat(), f"{RK}|1h": AT.isoformat()}
    seed.state["refreshed_at"] = AT.isoformat()
    seed._save(RK, "card", "24h", "/sample", "card-24h", card_html(IDS))
    odds24 = seed._save(RK, "odds_win", "24h", "/sample", "win-24h", win_html())
    at[0] = AT
    seed._save(RK, "card", "1h", "/sample", "card-1h", card_html(IDS))
    seed._write_state()
    return odds24["path"]


def test_unreadable_page_of_a_past_time_point_does_not_block_the_due_one(tmp_path):
    """締切を過ぎた24時間前のオッズのページだけが読めなくても、締切前の1時間前の入力は置く（そのページは読まない）。"""
    paths = _paths(tmp_path)
    bad = _seed_24h_then_1h(paths)
    assert mam.unplaced(paths, AT.replace(minute=35)) == {(RK, TP)}   # 24時間前は締切を過ぎている
    real = wallet._read
    touched = []

    def read(root, rec):
        touched.append(rec["path"])
        if rec["path"] == bad:
            raise OSError(errno.EIO, "古いページだけ読めない")
        return real(root, rec)

    with patch.object(wallet, "_read", read):
        out = _tick(paths, [AT.replace(minute=35)])
    assert out["saved"] == 0 and out["mam_packets"] == 1
    assert bad not in touched                                   # 置く時点に使わないページは読まない
    _placed_once(paths)


def test_unreadable_page_of_a_past_time_point_does_not_block_a_tick_with_new_pages(tmp_path):
    """新しいページを取った回（ふつうの書き出し）も同じ：置くのは締切前で置けていない時点だけで、その時点のページだけ読む。"""
    paths = _paths(tmp_path)
    bad = _seed_24h_then_1h(paths)
    state_path = tmp_path / "live" / "state.json"
    state = json.loads(state_path.read_text())
    state["taken"].pop(f"{RK}|1h")   # 1時間前のページは、この回に取る
    state.pop("refreshed_at")
    state_path.write_text(json.dumps(state, ensure_ascii=False))
    real = wallet._read

    def read(root, rec):
        if rec["path"] == bad:
            raise OSError(errno.EIO, "古いページだけ読めない")
        return real(root, rec)

    with patch.object(wallet, "_read", read):
        out = _tick(paths, [AT.replace(minute=33)])
    assert out["saved"] >= 1 and out["mam_packets"] == 1
    _placed_once(paths)


def test_counting_and_placing_use_the_same_pages(tmp_path):
    """数える側と置く側の条件が同じ：締切前で置けていないと数えた時点は、手の書き出しでも締切つきでも同じ組が置かれる。
    15分前は、その時点のオッズのページが無ければ数えず、置きもしない。"""
    paths = _paths(tmp_path)
    live = paths.data_dir / "live"
    at = [AT]
    seed = collector.Collector(live, client=_client(), now=lambda: at[0])
    for rk, post in (("2025-08-30|京都|11", "15:40"), ("2025-08-30|京都|12", "15:48"), ("2025-08-30|京都|10", "15:44")):
        seed.state["races"][rk] = {"race_number": int(rk[-2:]), "post_time": post, "race_date": "2025-08-30", "venue": "京都",
                                   "field_size": 8, "known_at": AT.isoformat()}
        seed._save(rk, "card", "1h", "/sample", "card", card_html(IDS))
    at[0] = AT.replace(hour=15, minute=20)   # 15分前の窓：11R（締切 15:25）はオッズあり、12R（締切 15:33）は出馬表だけ
    seed._save("2025-08-30|京都|11", "card", "15m", "/sample", "card", card_html(IDS))
    seed._save("2025-08-30|京都|11", "odds_trio", "15m", "/sample", "trio", trio_html(time="15時19分"))
    seed._save("2025-08-30|京都|12", "card", "15m", "/sample", "card", card_html(IDS))
    seed.state["refreshed_at"] = at[0].isoformat()
    seed._write_state()
    now = AT.replace(hour=15, minute=21)
    due = mam.unplaced(paths, now)
    assert due == {("2025-08-30|京都|11", "15m")}                    # 1時間前は締切後、12R の15分前はオッズが無い
    placed = {(json.loads(p.read_text())["race_key"], json.loads(p.read_text())["time_point"])
              for p in mam.export_packets(paths, now=lambda: now)}
    assert placed == due


def test_counting_time_is_recorded(tmp_path):
    """数えるのにかかった時間（mam_check_seconds）を毎回残し、置き直した回の時間（mam_retry_seconds）は数える時間も含む。"""
    paths = _paths(tmp_path)
    clock = [AT]
    _unplaced_pages(paths, clock, fresh=True)
    real = mam.unplaced

    def slow(*a, **k):
        time.sleep(0.05)
        return real(*a, **k)

    clock[0] = AT.replace(minute=31)   # 新しいページの無い回（12R の窓は 14:33 から）
    with patch.object(mam, "unplaced", slow):
        out = _tick(paths, clock, another=True)
    assert out["saved"] == 0 and out["mam_packets"] == 1 and out["mam_check_seconds"] >= 0.05
    if "live" not in out:   # 置き直しの回（予想を流さなかった回。PR #15 と合わせた版は前の回の失敗で予想を流し直す）
        assert out["mam_retry_seconds"] >= out["mam_check_seconds"]
    clock[0] = AT.replace(minute=32)
    out = _tick(paths, clock, another=True)                         # 置くものが無い回も、数えた時間は残る
    assert "mam_check_seconds" in out and "mam_retry_seconds" not in out and out.get("mam_packets", 0) == 0


def test_due_time_point_whose_own_page_is_unreadable_is_shown_and_retried(tmp_path):
    """置く時点そのもののページが読めないときは、置けない理由を見回りの結果（mam_held）に出し、読めるようになった回に置く。"""
    paths = _paths(tmp_path)
    _seed_24h_then_1h(paths)
    real = wallet._read

    def read(root, rec):
        if rec["kind"] == "card" and rec["time_point"] == TP:
            raise OSError(errno.EIO, "1時間前の出馬表が読めない")
        return real(root, rec)

    with patch.object(wallet, "_read", read):
        out = _tick(paths, [AT.replace(minute=35)])
    assert out["mam_packets"] == 0 and "OSError" in out["mam_held"][f"{RK}|{TP}"]
    assert not mam._packet_path(paths, RK, TP).exists()
    out = _tick(paths, [AT.replace(minute=37)])
    assert out["mam_packets"] == 1 and "mam_held" not in out
    _placed_once(paths)


def test_tick_with_new_pages_always_reaches_the_export_step(tmp_path):
    """新しいページを取って予想・精算を流した回は、置くものが無くても書き出しを呼ぶ（予想・精算の後の書き出しの位置を変えない。
    書き出しは置けていない時点が無ければ何も読まずに戻る）。"""
    paths = _paths(tmp_path)
    calls = []
    with patch.object(mam, "unplaced", lambda *a, **k: set()), \
         patch.object(mam, "export_packets", lambda p, **k: calls.append(k.get("only")) or []):
        out = _tick(paths, [AT])
    assert out["saved"] == 3 and out["mam_packets"] == 0 and calls == [set()]
    assert mam.export_packets(paths, now=AT, only=set()) == []


# ---- 💚20261126-3：別の見回りが、置く前に発走時刻・ページを変える ----

def _moved_client(post):
    """発走時刻だけを変えた合成のページ（11R）。"""
    client = _client()
    load = client.pages
    client.pages = lambda tag: load(tag).replace("15時40分", f"{post[:2]}時{post[3:]}分") if tag.startswith("pw01drl") else load(tag)
    return client


def _other_tick(paths, when, post):
    """同じ置き場で、別の見回りを1回動かす（発走時刻を post に変えた一覧を読む）。"""
    real = REAL_COLLECTOR
    refresh = real.refresh
    buf = io.StringIO()
    with patch.object(collector, "Collector", side_effect=lambda root: real(root, client=_moved_client(post), now=lambda: when)), \
         patch.object(real, "refresh", lambda self, force=False: refresh(self, force=True)), \
         patch.object(wallet, "run_live", return_value={"settled": 0}), patch.object(cli, "_notify"), redirect_stdout(buf):
        cli.cmd_collect(paths, SimpleNamespace(action="tick"))
    return json.loads(buf.getvalue().splitlines()[-1])


def _tick_interrupted_by(paths, start, resume, between):
    """start に始めた見回りが 11R の入力を書き終えた所で止まり、その間に between() が動き、resume に再開する。"""
    clock = [start]
    real = mam._write_complete
    seen, started = [], []

    def write(path, text):
        real(path, text)
        if not started and json.loads(text)["race_key"] == RK:   # 1回だけ（間に動く見回りの書き出しでは止めない）
            started.append(1)
            seen.append(between())
            clock[0] = resume

    with patch.object(mam, "_write_complete", write):
        out = _tick(paths, clock)
    return out, seen


def _pages_saved_unplaced(paths):
    """14:30 に 11R のページを保存したが、入力は置けずに終わった状態。"""
    def eio(*_a, **_k):
        raise OSError(errno.EIO, "置き場が一時的に使えない")
    with patch.object(mam, "_publish_once", eio):
        assert _tick(paths, [AT])["saved"] == 3
    assert not mam._packet_path(paths, RK, TP).exists()


def test_post_time_moved_earlier_by_another_tick_is_not_placed_with_the_old_deadline(tmp_path):
    """14:33 の見回りが 11R の入力を書き終えた後、14:38 の別の見回りが発走を 15:37（締切 14:37）に繰り上げて保存した。
    再開した先の見回りは、最新の締切（14:37）を過ぎているので置かない（古い締切 14:40 の入力を置かない）。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)
    out, seen = _tick_interrupted_by(paths, AT.replace(minute=33), AT.replace(minute=38),
                                     lambda: _other_tick(paths, AT.replace(minute=38), "15:37"))
    post = json.loads((tmp_path / "live" / "state.json").read_text())["races"][RK]["post_time"]
    if seen[0].get("skipped"):   # PR #15 と合わせた版：見回りの鍵で、別の見回りは何もしない（繰り上げはまだ知らない）
        assert post == "15:40" and out["mam_packets"] == 1
        assert json.loads(mam._packet_path(paths, RK, TP).read_text())["deadline"].endswith("T14:40+09:00")
    else:
        assert seen[0].get("mam_packets", 0) == 0                      # 別の見回りは締切後なので置かない
        assert post == "15:37"
        assert out["mam_packets"] == 0 and not mam._packet_path(paths, RK, TP).exists()
    _no_leftovers(paths)


@pytest.mark.parametrize("other_places", [True, False], ids=["other-tick-places", "other-tick-cannot-place"])
def test_post_time_moved_later_by_another_tick_is_placed_with_the_new_deadline(tmp_path, other_places):
    """別の見回りが発走を 15:45（締切 14:45）に繰り下げたら、置かれる入力は新しい締切のもの1つだけ。
    別の見回りが自分で置ければそれが残り、先の見回りは置かない。別の見回りが置けなければ、先の見回りが新しい状態で作り直して置く。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)

    def other():
        if other_places:
            return _other_tick(paths, AT.replace(minute=36), "15:45")
        with patch.object(mam, "_publish_once", side_effect=OSError(errno.EIO, "置き場が一時的に使えない")):
            return _other_tick(paths, AT.replace(minute=36), "15:45")
    out, seen = _tick_interrupted_by(paths, AT.replace(minute=33), AT.replace(minute=36), other)
    packet = json.loads(mam._packet_path(paths, RK, TP).read_text())
    if seen[0].get("skipped"):   # PR #15 と合わせた版：見回りの鍵で、別の見回りは何もしない
        assert out["mam_packets"] == 1 and packet["deadline"].endswith("T14:40+09:00")
        _no_leftovers(paths)
        return
    assert seen[0].get("mam_packets", 0) == (1 if other_places else 0)
    assert out["mam_packets"] == (0 if other_places else 1)
    assert packet["deadline"].endswith("T14:45+09:00") and packet["race"]["post_time"] == "15:45"
    assert mam.issued_roster(paths, RK, TP) == list(range(1, 9))
    _no_leftovers(paths)


def test_page_saved_by_another_tick_meanwhile_is_used_in_the_remade_input(tmp_path):
    """締切は同じでも、置く前に別の見回りがその時点の出馬表を保存し直したら、新しいページで作り直して置く。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)

    def resave():
        col = REAL_COLLECTOR(tmp_path / "live", client=_client(), now=lambda: AT.replace(minute=35))
        return col._save(RK, "card", TP, "/sample", "card-again", card_html(IDS) + "<!-- 取り直し -->")
    out, seen = _tick_interrupted_by(paths, AT.replace(minute=33), AT.replace(minute=35), resave)
    assert out["mam_packets"] == 1
    packet = json.loads(mam._packet_path(paths, RK, TP).read_text())
    assert packet["source_snapshots"][0] == seen[0]["sha256"] and packet["deadline"].endswith("T14:40+09:00")
    _no_leftovers(paths)


def test_remaking_stops_when_the_state_keeps_changing(tmp_path):
    """置く直前に毎回変わっているときは、決まった回数だけ作り直して止まり、次の見回りで置く。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)
    made = []
    real_make = mam._make

    def make(*a, **k):
        made.append(1)
        return real_make(*a, **k)

    def always_changed(*_a):
        def check():
            raise mam._Stale("changed")
        return check

    with patch.object(mam, "_make", make), patch.object(mam, "_recheck", always_changed):
        out = _tick(paths, [AT.replace(minute=33)])
    assert out["mam_packets"] == 0 and len(made) == mam.REMAKE_LIMIT and not mam._packet_path(paths, RK, TP).exists()
    _no_leftovers(paths)
    assert _tick(paths, [AT.replace(minute=35)])["mam_packets"] == 1
    _placed_once(paths)


def test_one_input_reads_each_page_once(tmp_path):
    """置き直しの1つの入力で、使うページ（その時点の出馬表）は1回だけ読む（ほかの時点のために読み直さない）。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)
    real = wallet._read
    reads = []

    def read(root, rec):
        import traceback   # まむへの書き出しから読んだものだけ数える（PR #15 と合わせた版は、ほかの読み手も同じページを読む）
        if any(f.filename.endswith("live/mam.py") for f in traceback.extract_stack()):
            reads.append(rec["path"])
        return real(root, rec)

    with patch.object(wallet, "_read", read):
        out = _tick(paths, [AT.replace(minute=33)])
    assert out["mam_packets"] == 1
    assert len(reads) == len(set(reads)) and reads
    _placed_once(paths)


def test_manual_export_uses_the_latest_post_time_when_it_changes_meanwhile(tmp_path):
    """手の mam export は締切を問わないが、置く前に別の見回りが発走時刻を変えたら、新しい状態で作り直す（入力の締切の欄が最新になる）。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)
    real = mam._write_complete
    started = []

    def write(path, text):
        real(path, text)
        if not started:   # 書き終えた所で、別の見回りが発走時刻を 15:45 に変えて保存した
            started.append(1)
            state_path = tmp_path / "live" / "state.json"
            state = json.loads(state_path.read_text())
            state["races"][RK]["post_time"] = "15:45"
            state_path.write_text(json.dumps(state, ensure_ascii=False))

    with patch.object(mam, "_write_complete", write), redirect_stdout(io.StringIO()):
        cli.cmd_mam(paths, SimpleNamespace(action="export"))
    packet = json.loads(mam._packet_path(paths, RK, TP).read_text())
    assert packet["deadline"].endswith("T14:45+09:00") and packet["race"]["post_time"] == "15:45"
    _no_leftovers(paths)


def test_manual_export_keeps_the_pages_it_read_when_only_pages_change(tmp_path):
    """手の mam export は、そのときあるページで置く。置く前に別の見回りがページを取り直しても作り直さない（先に作った完全な入力を置く）。"""
    paths = _paths(tmp_path)
    _pages_saved_unplaced(paths)
    first_card = next(json.loads(x)["sha256"] for x in (tmp_path / "live" / "snapshots.jsonl").read_text().splitlines()
                      if json.loads(x)["kind"] == "card")
    real = mam._write_complete
    started = []

    def write(path, text):
        real(path, text)
        if not started:
            started.append(1)
            REAL_COLLECTOR(tmp_path / "live", client=_client(), now=lambda: AT.replace(minute=35))._save(
                RK, "card", TP, "/sample", "card-again", card_html(IDS) + "<!-- 取り直し -->")

    with patch.object(mam, "_write_complete", write), redirect_stdout(io.StringIO()):
        cli.cmd_mam(paths, SimpleNamespace(action="export"))
    packet = json.loads(mam._packet_path(paths, RK, TP).read_text())
    assert packet["source_snapshots"][0] == first_card and packet["deadline"].endswith("T14:40+09:00")
    _no_leftovers(paths)
