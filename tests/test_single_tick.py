"""見回り（collect tick）は1つずつ。重なると状態の書込みで落ち、取得ずみの記録も消える。"""
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from keiba import cli
from keiba.live import collector as c
from keiba.live.collector import Collector

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: None)
    return types.SimpleNamespace(data_dir=tmp_path)


def tick(paths):
    cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))


def test_second_tick_is_skipped_while_one_is_running(paths, monkeypatch, capsys):
    inside, release, calls = threading.Event(), threading.Event(), []

    def slow(self, log=print, notify=None):
        calls.append(1)
        if len(calls) == 1:
            inside.set()
            release.wait(10)
        return {}
    monkeypatch.setattr(Collector, "tick", slow)
    first = threading.Thread(target=tick, args=(paths,))
    first.start()
    assert inside.wait(10)
    tick(paths)
    during = len(calls)
    release.set()
    first.join(10)
    assert during == 1 and "skipped" in capsys.readouterr().out
    tick(paths)
    assert len(calls) == 2


def test_lock_is_released_after_exception(paths, monkeypatch):
    calls = []

    def boom(self, log=print, notify=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("途中で失敗")
        return {}
    monkeypatch.setattr(Collector, "tick", boom)
    with pytest.raises(RuntimeError):
        tick(paths)
    tick(paths)
    assert len(calls) == 2


def test_lock_is_released_when_process_is_killed(paths, monkeypatch):
    code = ("import sys,time,types;from keiba import cli;from keiba.live.collector import Collector;"
            "Collector.tick=lambda self,**k:(print('in',flush=True),time.sleep(60));"
            "cli.cmd_collect(types.SimpleNamespace(data_dir=__import__('pathlib').Path(sys.argv[1])),types.SimpleNamespace(action='tick'))")
    p = subprocess.Popen([sys.executable, "-c", code, str(paths.data_dir)], stdout=subprocess.PIPE, text=True,
                         env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    try:
        assert p.stdout.readline().strip() == "in"
        calls = []
        monkeypatch.setattr(Collector, "tick", lambda self, **k: calls.append(1) or {})
        tick(paths)
        assert not calls                  # 別のプロセスが動いている間は動かない
        p.kill()
        p.wait(10)
        tick(paths)
        assert calls                      # 強制終了されても鍵は残らない
    finally:
        p.kill()
        p.wait(10)
