"""まむへの入力を置けなかった時点を、次の見回りで締切前に置き直す（みに💚20261125-19・-22・-23 の指摘。PR #18）。

見回り（keiba collect tick）は、新しいページを取った回だけ予想と書き出しをする。
入力を置く途中で止まる・置き場やページが一時的に使えないと、次に新しいページを取るまで置かれず、締切を過ぎることがあった。
いまは、出馬表のページを保存してある時点のうち締切前なのに入力が無いものを毎回数え（mam.unplaced）、
あれば新しいページが無い回でも、その時点だけを書き出す。見回りは締切を過ぎた時点を置かない。
"""
import errno
import io
import json
import multiprocessing as mp
import os
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from keiba import cli
from keiba.config import Paths
from keiba.live import collector, mam

RK = "2025-08-30|京都|11"
TP = "1h"
AT = datetime(2025, 8, 30, 14, 30, tzinfo=collector.JST)   # 1時間前の締切は 14:40（発走 15:40）


def _client(another=False, down=False):
    """合成のページ。another で 12R（発走 15:48・1時間前の締切 14:48）も載せる。down は取得が全部一時エラー。"""
    import re
    from tests.test_live_collector import INDEX, FakeClient, race_list
    from tests.test_live_wallet import card_html, trio_html, win_html
    listing = race_list(odds=True).replace("18頭", "8頭").replace("2026年11月22日", "2025年8月30日")
    if another:
        first = re.search(r"<tr>.*?</tr>", listing, flags=re.DOTALL).group()
        listing = listing.replace(first, first + first.replace("11レース", "12レース").replace("15時40分", "15時48分")
                                  .replace("506112026", "506122026").replace("pw15orl10/AA", "pw15orl20/AA"))
    win = win_html() + """<a href='#' onclick="return doAction('/JRADB/accessO.html', 'pw156ou1/66');">3連複</a>"""
    card = card_html([f"{n:010}" for n in range(8)])
    pages = {"pw01drl00082026050620261122/28": listing, "pw01dde0108202605061120261122/55": card,
             "pw01dde0108202605061220261122/55": card, "pw15orl10/AA": win, "pw15orl20/AA": win, "pw156ou1/66": trio_html()}

    def fetch(tag):
        if down:
            raise OSError(errno.EIO, "一時的に取れない")
        return INDEX if tag.startswith("pw01dli") else pages[tag]
    return FakeClient(fetch)


def _tick(paths, when, **client):
    """ふだんの入口（cli の collect tick）を、合成のページと時計で1回動かす。最後の行（結果）と知らせの数を返す。"""
    real = collector.Collector
    buf = io.StringIO()
    with patch.object(collector, "Collector", side_effect=lambda root: real(root, client=_client(**client), now=lambda: when)), \
         patch.object(cli, "_notify") as notify, redirect_stdout(buf):
        cli.cmd_collect(paths, SimpleNamespace(action="tick"))
    return json.loads(buf.getvalue().splitlines()[-1]), notify.call_count


def _paths(tmp_path):
    from tests.conftest import make_tables
    paths = Paths(data_dir=tmp_path)
    races, runners = make_tables(n_days=34, races_per_day=6, field=10, horses=60)
    runners["horse_id"] = runners["horse_id"].map(lambda h: f"{int(h[1:]):010d}")
    paths.tables_dir.mkdir(parents=True)
    races.to_parquet(paths.tables_dir / "races.parquet")
    runners.to_parquet(paths.tables_dir / "runners.parquet")
    return paths


def _no_links(*_):
    raise OSError(errno.ENOTSUP, "hard links are not supported here")


SEVEN = json.dumps({"race_key": RK, "time_point": TP, "horses": [{"horse_number": n} for n in range(1, 8)]})


def _placed_once(paths, first=None):
    """書き終えた8頭の入力が1つだけあり、後から別の入力では入れ替わらない。"""
    f = mam._packet_path(paths, RK, TP)
    assert mam.issued_roster(paths, RK, TP) == list(range(1, 9))
    assert json.loads(f.read_text())["deadline"].endswith("T14:40+09:00")
    body = f.read_bytes()
    assert first is None or body == first
    assert mam._publish_once(paths, RK, TP, SEVEN) is None and f.read_bytes() == body
    assert [p.name for p in f.parent.iterdir() if not p.name.startswith(".")] == [f.name]
    return body


def _child_tick_then_die(data_dir, where):
    """別プロセスで最初の見回りを動かし、ハードリンクが使えない置き場で入力を置く途中に強制終了する。"""
    from pathlib import Path
    paths = Paths(data_dir=Path(data_dir))
    real_write = mam._write_complete
    calls = []

    def write(tmp, text):
        calls.append(tmp)
        if where == "half-written" and len(calls) == 2:   # outbox の中の隠し名に、一部だけ書いたところで止まる
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            os.write(fd, text[:29].encode())
            os._exit(9)
        return real_write(tmp, text)

    def die(src, dst):   # 書き終えた隠し名を、正式な名前へ移す直前に止まる
        os._exit(9)

    with patch.object(mam.os, "link", _no_links), patch.object(mam, "_write_complete", write), \
         patch.object(mam, "_rename_noreplace", die if where == "before-rename" else mam._rename_noreplace):
        _tick(paths, AT)
    os._exit(3)   # 止まらなかった


@pytest.mark.parametrize("where", ["half-written", "before-rename"])
def test_quiet_tick_republishes_an_interrupted_packet_before_the_deadline(tmp_path, where):
    paths = _paths(tmp_path)
    child = mp.get_context("spawn").Process(target=_child_tick_then_die, args=(str(tmp_path), where))
    child.start()
    child.join(300)
    assert child.exitcode == 9
    f = mam._packet_path(paths, RK, TP)
    assert not f.exists()   # 正式な名前には何も無い
    with patch.object(mam.os, "link", _no_links):   # 置き場はリンクが使えないまま
        out, _ = _tick(paths, AT.replace(minute=33))   # 新しいページの無い、締切前の見回り
        assert out["saved"] == 0 and out["mam_packets"] >= 1
        first = _placed_once(paths)
        out, _ = _tick(paths, AT.replace(minute=37))
        assert out.get("mam_packets", 0) == 0   # 置き終えたら、静かな回は書き出さない
    _placed_once(paths, first)


def test_failed_prediction_still_places_the_packets_in_the_same_tick(tmp_path):
    paths = _paths(tmp_path)

    def boom(_):
        raise RuntimeError("予想の失敗")
    with patch("keiba.live.wallet.run_live", boom):
        out, told = _tick(paths, AT)
    assert out["saved"] == 3 and "live_error" in out and out["mam_packets"] >= 1 and told == 1
    _placed_once(paths)


def test_page_unreadable_for_one_tick_is_placed_by_a_later_quiet_tick(tmp_path):
    """ページを一時的に読めず、その回は0件で「成功」しても、置かれるまで次の回にやり直す（💚20261125-22 要修正②）。"""
    paths = _paths(tmp_path)
    real = mam.live_tables
    with patch.object(mam, "live_tables", lambda root, state, snaps: (None, None, {})):   # 出馬表を読めず、入力が作れない
        out, _ = _tick(paths, AT)
    assert out["saved"] == 3 and out["mam_packets"] == 0 and not mam._packet_path(paths, RK, TP).exists()
    with patch.object(mam, "live_tables", lambda root, state, snaps: (None, None, {})):
        out, _ = _tick(paths, AT.replace(minute=33))   # まだ読めない回も、収集は続け、置けていないことを覚えておく
    assert out["saved"] == 0 and out["mam_packets"] == 0
    assert mam.live_tables is real
    out, _ = _tick(paths, AT.replace(minute=37))   # 読めるようになった静かな回
    assert out["saved"] == 0 and out["mam_packets"] >= 1
    _placed_once(paths)


def test_outbox_unusable_for_a_while_is_placed_after_it_recovers(tmp_path):
    """まむの置き場だけが一時的に使えなくても、収集は続け、使えるようになった回に置く（💚20261125-22 要修正③）。"""
    paths = _paths(tmp_path)

    def eio(*_):
        raise OSError(errno.EIO, "置き場が一時的に使えない")
    with patch.object(mam, "_publish_once", eio):
        out, told = _tick(paths, AT)
        assert out["saved"] == 3 and "mam_error" in out and told >= 1   # ページは保存し、知らせる
        out, told = _tick(paths, AT.replace(minute=35))
        assert out["saved"] == 0 and "mam_error" in out and told == 1
    out, told = _tick(paths, AT.replace(minute=39))   # 戻った回（締切 14:40 の前）
    assert out["saved"] == 0 and out["mam_packets"] >= 1 and told == 0
    _placed_once(paths)


def test_an_older_overlapping_tick_cannot_cancel_a_later_retry(tmp_path):
    """見回りが重なっても、置けていない入力の記録を誰かが消すことは無い（💚20261125-22 要修正①）。
    印を使わず、毎回「締切前なのに入力が無い時点」を数えるので、先の回が後の回の記録を消す場面が無い。"""
    paths = _paths(tmp_path)
    with patch.object(mam, "export_packets", lambda *a, **k: []):   # 14:30 の回はページを保存したが入力を置けずに終わる
        out, _ = _tick(paths, AT)
    assert out["saved"] == 3 and not mam._packet_path(paths, RK, TP).exists()
    placed = 0
    for minute in (20, 35):   # 遅れて終わった古い回（14:20 の時計）と、その後の回。どちらが置いてもよい
        out, _ = _tick(paths, AT.replace(minute=minute))
        assert out["saved"] == 0
        placed += out.get("mam_packets", 0)
    assert placed >= 1
    _placed_once(paths)


def test_unplaced_counts_only_before_the_deadline(tmp_path):
    """置けていない入力の数え方：置けていれば無し、無ければ締切前だけ「ある」、締切を過ぎたら無し。"""
    paths = _paths(tmp_path)
    out, _ = _tick(paths, AT)   # ふつうに置ける
    assert out["mam_packets"] >= 1
    assert (RK, TP) not in mam.unplaced(paths, AT.replace(minute=33))     # 置けている
    mam._packet_path(paths, RK, TP).unlink()
    assert (RK, TP) in mam.unplaced(paths, AT.replace(minute=33))         # 締切 14:40 の前で、無い
    assert (RK, TP) not in mam.unplaced(paths, AT.replace(minute=41))     # 締切を過ぎた時点は置き直さない
    out, _ = _tick(paths, AT.replace(minute=34))
    assert out["mam_packets"] >= 1 and "mam_retry_seconds" in out
    _placed_once(paths)
    # 締切の分からない時点（発走時刻が空・日付が壊れている）は数えない。毎回の書き出しの空回りにしない
    state_path = tmp_path / "live" / "state.json"
    state = json.loads(state_path.read_text())
    rows = [json.loads(x) for x in (tmp_path / "live" / "snapshots.jsonl").read_text().splitlines()]
    card = next(r for r in rows if r["kind"] == "card")
    extra = {"2025-08-30|京都|12": {"race_date": "2025-08-30", "post_time": None},
             "2025-08-30|京都|10": {"race_date": "2025-08-30"},
             "2025-08-30|京都|9": {"race_date": "あした", "post_time": "15:00"}}
    state["races"].update(extra)
    state_path.write_text(json.dumps(state, ensure_ascii=False))
    with open(tmp_path / "live" / "snapshots.jsonl", "a") as fh:
        for rk in extra:
            fh.write(json.dumps(dict(card, race_key=rk), ensure_ascii=False) + "\n")
    assert mam.unplaced(paths, AT.replace(minute=35)) == set()


def test_quiet_tick_places_only_the_time_points_still_before_their_deadline(tmp_path):
    """締切前の欠けと締切後の欠けが混ざっても、締切を過ぎた時点は置かない（💚20261125-23 要修正①）。"""
    paths = _paths(tmp_path)
    with patch.object(mam, "export_packets", lambda *a, **k: []):   # 11R は 14:30、12R は 14:33 にページを保存したが、入力は置けずに終わる
        out, _ = _tick(paths, AT, another=True)
        assert out["saved"] == 3
        out, _ = _tick(paths, AT.replace(minute=33), another=True)
        assert out["saved"] == 3
    k11, k12 = ("2025-08-30|京都|11", TP), ("2025-08-30|京都|12", TP)
    out, _ = _tick(paths, AT.replace(minute=41), another=True)   # 11R の締切 14:40 は過ぎ、12R の締切 14:48 の前
    assert out["saved"] == 0 and out["mam_packets"] >= 1
    assert mam.issued_roster(paths, *k12) == list(range(1, 9))
    assert not mam._packet_path(paths, *k11).exists()              # 締切後の時点は、同じ回でも置かない
    assert k11 not in mam.unplaced(paths, AT.replace(minute=41))
    out, _ = _tick(paths, AT.replace(minute=49), another=True)     # 両方の締切の後：置くものが無い
    assert "mam_packets" not in out and "mam_retry_seconds" not in out


def test_pages_saved_but_not_marked_taken_are_still_placed(tmp_path):
    """ページを保存した後、取得済みの記録を書く前に止まっても、静かな見回りが保存したページから置く（💚20261125-23 要修正②）。"""
    paths = _paths(tmp_path)
    with patch.object(mam, "export_packets", lambda *a, **k: []):
        out, _ = _tick(paths, AT)
    assert out["saved"] == 3
    state_path = tmp_path / "live" / "state.json"
    state = json.loads(state_path.read_text())
    state["taken"] = {}   # 取得済みの記録を書く前に止まった
    state_path.write_text(json.dumps(state, ensure_ascii=False))
    out, _ = _tick(paths, AT.replace(minute=35), down=True)        # 取り直しは一時エラーで0件
    assert out["saved"] == 0 and out["mam_packets"] >= 1
    _placed_once(paths)


def test_manual_export_ignores_the_deadline(tmp_path):
    """手で動かす mam export は締切を問わず、まだ無い入力を置く（復旧のため）。見回りは締切を過ぎた時点を置かない。"""
    paths = _paths(tmp_path)
    with patch.object(mam, "export_packets", lambda *a, **k: []):
        _tick(paths, AT)
    out, _ = _tick(paths, AT.replace(minute=45))
    assert not mam._packet_path(paths, RK, TP).exists()            # 見回り：締切後は置かない
    with redirect_stdout(io.StringIO()):
        cli.cmd_mam(paths, SimpleNamespace(action="export"))
    _placed_once(paths)


def test_tick_goes_on_when_the_outbox_cannot_even_be_checked(tmp_path):
    """置けていないかを調べられないとき（権限など）も、収集は止めずに書き出しを試し、失敗を知らせる。"""
    paths = _paths(tmp_path)
    out, _ = _tick(paths, AT)
    assert out["mam_packets"] >= 1

    def denied(*_):
        raise PermissionError(errno.EACCES, "調べられない")
    with patch.object(mam, "_packet_path", denied):
        out, told = _tick(paths, AT.replace(minute=33))
    assert out["saved"] == 0 and "mam_error" in out and told == 1
