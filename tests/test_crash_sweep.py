"""止める・重ねる試験の短い版（ふだんの試験で毎回流す）。全部の点は tools/crash_sweep.py（CI の crash-sweep）。

ふつうの見回りの入口で1日を流し、代表の書き込み（台帳の確定・目録の追記・状態の置き換え・まむの入力の置き場・
月次の目印・モデルの控え）の前後で1回だけ死んでも、最後の結果が止めなかった1日と同じになることを確かめる。
"""
import json

import pytest

from tests.crash_harness import exporter, invariants, readers, run_day


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    day = run_day(tmp_path_factory.mktemp("base") / "day")
    return day, json.loads(json.dumps(invariants(day), default=str))


def test_baseline_day_is_whole(baseline):
    day, inv = baseline
    assert not day.died and inv["review_matches_ledger"] and not inv["stale_months"]
    assert inv["mam_json_complete"] and inv["models_loadable"] and not inv["taken_without_material"]
    assert inv["settled"] and inv["counted"]


# 代表の書き込み（ラベルの一部, 前後）。同じ種類の最初の点で止める
POINTS = [("ledger commit", "after"), ("ledger commit", "before"), ("append snapshots.jsonl", "before"),
          ("replace state.json", "after"), ("link ", "before"), ("marks.json", "before"), (".pkl", "before"),
          ("append post_times.jsonl", "after")]


@pytest.mark.parametrize("label, when", POINTS)
def test_one_death_does_not_change_the_day(baseline, tmp_path, label, when):
    day, inv = baseline
    ks = [i + 1 for i, x in enumerate(day.log) if label in x]
    assert ks, f"{label} の書き込みが1日に無い"
    models = day.root / "models"
    for k in ks[:1]:
        died = run_day(tmp_path / f"k{k}", crash_at=k, when=when, models=None if label == ".pkl" else models)
        assert died.died, "止まらなかった"
        got = json.loads(json.dumps(invariants(died), default=str))
        if got != inv:   # 止まった見回りが丸ごと無かった1日と同じなら正しい（取り直しはその時のページで行う）
            alt = json.loads(json.dumps(invariants(run_day(tmp_path / f"s{k}", models=models, skip=died.died[0][0])),
                                        default=str))
            assert got == alt, {key: (got[key], inv[key], alt[key]) for key in got if got[key] != inv[key]}


def test_readers_never_see_a_drop(baseline, tmp_path):
    """書き込みの直前ごとに確認アプリ・CLI・月次が読んでも、失敗せず、数えた予想・財布・月次が途中で減らない。"""
    day, inv = baseline
    run = run_day(tmp_path / "r", models=day.root / "models", probe=readers)
    seen = [s for s in run.probe.seen if "error" not in s]
    errors = [s for s in run.probe.seen if "error" in s]
    assert not errors, errors[:3]
    for a, b in zip(seen, seen[1:]):
        assert a["counted"] <= b["counted"], (b["k"], b["what"])
        assert a["shown"] <= b["shown"], (b["k"], b["what"])
        for part in ("money", "month"):   # 賭け金・払戻のどちらも減らない
            for key, (st, rt) in a[part].items():
                st2, rt2 = b[part].get(key, (0, 0))
                assert st2 >= st and rt2 >= rt, (part, b["k"], b["what"], key, (st, rt), (st2, rt2))
    assert json.loads(json.dumps(invariants(run), default=str)) == inv


def test_manual_mam_export_overlapping_every_write(baseline, tmp_path):
    """書き込みの直前ごとに手で mam export を割り込ませても、まむの入力はどれも完全で、1日の結果も同じ。"""
    day, inv = baseline
    run = run_day(tmp_path / "e", models=day.root / "models", probe=exporter)
    assert not run.probe.errors
    assert json.loads(json.dumps(invariants(run), default=str)) == inv
