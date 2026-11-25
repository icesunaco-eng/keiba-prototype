"""まむへの入力を置く途中で止まった後、次の見回りで置き直す（みに💚20261125-19 の指摘。PR #18）。

見回り（keiba collect tick）は、新しいページを取った回だけ予想と書き出しをする。
入力を置く途中で止まると、次に新しいページを取るまで置かれず、締切を過ぎることがあった。
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


def _client():
    from tests.test_live_collector import INDEX, FakeClient, race_list
    from tests.test_live_wallet import card_html, trio_html, win_html
    pages = {
        "pw01drl00082026050620261122/28": race_list(odds=True).replace("18頭", "8頭").replace("2026年11月22日", "2025年8月30日"),
        "pw01dde0108202605061120261122/55": card_html([f"{n:010}" for n in range(8)]),
        "pw15orl10/AA": win_html() + """<a href='#' onclick="return doAction('/JRADB/accessO.html', 'pw156ou1/66');">3連複</a>""",
        "pw156ou1/66": trio_html(),
    }
    return FakeClient(lambda tag: INDEX if tag.startswith("pw01dli") else pages[tag])


def _tick(paths, when):
    """ふだんの入口（cli の collect tick）を、合成のページと時計で1回動かす。最後の行（結果）を返す。"""
    real = collector.Collector
    buf = io.StringIO()
    with patch.object(collector, "Collector", side_effect=lambda root: real(root, client=_client(), now=lambda: when)), \
         patch.object(cli, "_notify"), redirect_stdout(buf):
        cli.cmd_collect(paths, SimpleNamespace(action="tick"))
    return json.loads(buf.getvalue().splitlines()[-1])


def _no_links(*_):
    raise OSError(errno.ENOTSUP, "hard links are not supported here")


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
    from tests.conftest import make_tables
    paths = Paths(data_dir=tmp_path)
    races, runners = make_tables(n_days=34, races_per_day=6, field=10, horses=60)
    runners["horse_id"] = runners["horse_id"].map(lambda h: f"{int(h[1:]):010d}")
    paths.tables_dir.mkdir(parents=True)
    races.to_parquet(paths.tables_dir / "races.parquet")
    runners.to_parquet(paths.tables_dir / "runners.parquet")
    child = mp.get_context("spawn").Process(target=_child_tick_then_die, args=(str(tmp_path), where))
    child.start()
    child.join(300)
    assert child.exitcode == 9
    f = mam._packet_path(paths, RK, TP)
    assert not f.exists()   # 正式な名前には何も無い
    assert (tmp_path / "mam" / "export-pending").exists()   # 置き終えていない印が残る

    with patch.object(mam.os, "link", _no_links):   # 置き場はリンクが使えないまま
        out = _tick(paths, AT.replace(minute=33))   # 新しいページの無い、締切前の見回り
        assert out["saved"] == 0 and out["mam_packets"] >= 1
        assert mam.issued_roster(paths, RK, TP) == list(range(1, 9))   # 締切 14:40 より前に、書き終えた8頭の入力
        assert not (tmp_path / "mam" / "export-pending").exists()
        first = f.read_bytes()
        assert "mam_packets" not in _tick(paths, AT.replace(minute=37))   # 置き終えたら、静かな回は書き出さない
        seven = json.dumps({"race_key": RK, "time_point": TP, "horses": [{"horse_number": n} for n in range(1, 8)]})
        assert mam._publish_once(paths, RK, TP, seven) is None   # 後から別の入力では入れ替わらない
    assert f.read_bytes() == first
    assert [p.name for p in f.parent.iterdir() if not p.name.startswith(".")] == [f.name]   # 正式な名前は書き終えた1つだけ


# ---- 印の付け外し（予想・書き出しは差し替えて、見回りの判断だけを見る）
class _FakeCollector:
    """見回りの代役。PR #15 の見回り（前の回の失敗をやり直す印）と合わせたときにも同じ判断を見られるよう、その入口も持つ。"""
    saved = []

    def __init__(self, root):
        self.n = 0

    def tick(self, notify=None):
        self.n = _FakeCollector.saved.pop(0)
        return {"saved": self.n}

    def live_pending(self):
        return bool(self.n)

    def live_started(self):
        return False

    def live_finished(self, failures, retry_now=True):
        return []


def _run(paths, saved, live=None, export=None):
    calls = {"live": 0, "export": 0}

    def run_live(_):
        calls["live"] += 1
        return live() if live else {"settled": 0}

    def export_packets(_):
        calls["export"] += 1
        return export() if export else []
    _FakeCollector.saved = [saved]
    buf = io.StringIO()
    with patch.object(collector, "Collector", _FakeCollector), patch.object(cli, "_notify") as notify, \
         patch("keiba.live.wallet.run_live", run_live), patch.object(mam, "export_packets", export_packets), redirect_stdout(buf):
        cli.cmd_collect(paths, SimpleNamespace(action="tick"))
    return calls, json.loads(buf.getvalue().splitlines()[-1]), notify.call_count


def test_quiet_tick_does_nothing_when_nothing_is_pending(tmp_path):
    paths = Paths(data_dir=tmp_path)
    calls, out, _ = _run(paths, 0)
    assert calls == {"live": 0, "export": 0} and not (tmp_path / "mam" / "export-pending").exists()
    calls, _, _ = _run(paths, 2)
    assert calls == {"live": 1, "export": 1} and not (tmp_path / "mam" / "export-pending").exists()


def test_failed_prediction_leaves_packets_for_the_next_quiet_tick(tmp_path):
    paths = Paths(data_dir=tmp_path)

    def boom():
        raise RuntimeError("予想の失敗")
    calls, out, _ = _run(paths, 3, live=boom)
    assert calls == {"live": 1, "export": 0} and "live_error" in out
    assert (tmp_path / "mam" / "export-pending").exists()
    calls, out, _ = _run(paths, 0)   # 新しいページは無いが、入力を置き直す（予想はやり直さない：main と同じ）
    assert calls == {"live": 0, "export": 1} and not (tmp_path / "mam" / "export-pending").exists()
    calls, _, _ = _run(paths, 0)
    assert calls == {"live": 0, "export": 0}


def test_failed_republish_keeps_the_mark_and_is_reported(tmp_path):
    paths = Paths(data_dir=tmp_path)
    (tmp_path / "mam").mkdir()
    (tmp_path / "mam" / "export-pending").touch()

    def no_place():
        raise OSError(errno.ENOTSUP, "置けない置き場")
    for _ in range(2):   # 置けない間は、毎回やり直して知らせる
        calls, out, told = _run(paths, 0, export=no_place)
        assert calls == {"live": 0, "export": 1} and "mam_error" in out and told == 1
        assert (tmp_path / "mam" / "export-pending").exists()
    calls, out, _ = _run(paths, 0)
    assert calls["export"] == 1 and not (tmp_path / "mam" / "export-pending").exists()


def test_mark_set_by_a_later_tick_is_not_removed(tmp_path):
    """重なって動いた後の見回りが付け直した印は、先の見回りが外さない。"""
    mark = tmp_path / "export-pending"
    mark.touch()
    mine = mark.stat().st_mtime_ns
    os.utime(mark, ns=(mine + 1_000_000, mine + 1_000_000))   # 別の見回りが付け直した
    cli._clear_mark(mark, mine)
    assert mark.exists()
    cli._clear_mark(mark, mark.stat().st_mtime_ns)
    assert not mark.exists()


def test_tick_goes_on_when_the_mark_cannot_be_written(tmp_path):
    """印を置けない置き場（data/mam がファイルになっているなど）でも、見回りは止めない。"""
    paths = Paths(data_dir=tmp_path)
    (tmp_path / "mam").write_text("not a folder")
    calls, out, _ = _run(paths, 2)
    assert calls == {"live": 1, "export": 1} and out["saved"] == 2
    calls, out, _ = _run(paths, 0)
    assert calls == {"live": 0, "export": 0} and out["saved"] == 0
