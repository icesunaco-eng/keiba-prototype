"""止める試験（全部の点）。tests/crash_harness.py の1日を、書き込みの1点ごと（操作の前・後）に止めて流し、
止めなかった1日と「崩れてはいけない決まり」を比べる。CI（.github/workflows/ci.yml の crash-sweep）でも流す。

使い方：python tools/crash_sweep.py [--jobs 4] [--out 結果.json]
終了コード：崩れた点が1つでもあれば 1。

途中で止まった見回りは「最後までやった1日」か「その見回りが丸ごと無かった1日」のどちらかと同じになるのが正しい
（止まった回の分は次の回に取り直すが、JRAのページは時刻で変わるので、取り直した中身は丸ごと無かった1日と同じになる）。
"""
from __future__ import annotations

import argparse
import multiprocessing
import json
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

def _plain(x):
    return json.loads(json.dumps(x, default=str))


def _one(args):
    k, when, base, skips, models, work = args   # models が空なら控え無し（モデルの控えを書く点を止める）
    from tests.crash_harness import run_day, invariants
    d = Path(tempfile.mkdtemp(dir=work))
    try:
        day = run_day(d / "x", crash_at=k, when=when, models=Path(models) if models else None)
        inv = _plain(invariants(day))
        diff = {key: inv[key] for key in inv if inv[key] != base[key]}
        tick = day.died[0][0] if day.died else None
        if diff and tick in skips:   # 丸ごと無かった1日と同じなら正しい
            alt = skips[tick]
            diff = {key: inv[key] for key in inv if inv[key] != alt[key]} and diff
        return {"k": k, "when": when, "died": day.died, "label": day.log[k - 1] if k <= len(day.log) else None,
                "diff": diff}
    except BaseException as exc:   # noqa: BLE001（道具の失敗も崩れた点として残す）
        return {"k": k, "when": when, "error": repr(exc)[:500]}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def judge(results: list[dict]) -> list[dict]:
    """崩れた点（道具の失敗・止まらなかった点も含む）。"""
    return [r for r in results if r.get("error") or r.get("diff") or not r.get("died")]


def _skip(args):
    hm, models, work = args
    from tests.crash_harness import run_day, invariants
    d = Path(tempfile.mkdtemp(dir=work))
    try:
        return hm, _plain(invariants(run_day(d / "x", models=Path(models), skip=hm)))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--shard", default="1/1", help="i/n：止める点を n 個に分けた i 番目だけ流す（CI で並べる）")
    a = ap.parse_args(argv)
    shard, shards = (int(x) for x in a.shard.split("/"))
    from tests.crash_harness import run_day, invariants
    work = Path(tempfile.mkdtemp(prefix="crash-sweep-"))
    try:
        trained = run_day(work / "trained")   # モデルを学習して控えを作る1日
        models = str(trained.root / "models")
        base_day = run_day(work / "base", models=Path(models))   # 止める点を数える1日（控えあり）
        base = _plain(invariants(base_day))
        assert base == _plain(invariants(trained)), "控えの有無で1日の結果が変わった"
        pkl = [i + 1 for i, x in enumerate(trained.log) if ".pkl" in x]   # モデルの控えを書く点（控え無しで止める）
        from tests.crash_harness import TICKS
        # 子は spawn で作る：親が LightGBM（OpenMP）を動かしたあとに fork すると、子が止まったまま動かないことがある（Linux）
        with ProcessPoolExecutor(a.jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
            skips = dict(pool.map(_skip, [(hm, models, str(work)) for hm in TICKS]))
            todo = [(k, w, base, skips, models, str(work)) for k in range(1, base_day.writes + 1)
                    for w in ("before", "after")] + [(k, w, base, skips, "", str(work)) for k in pkl
                                                      for w in ("before", "after")]
            todo = [x for i, x in enumerate(todo) if i % shards == shard - 1]
            results = []
            for r in pool.map(_one, todo):   # 1点ごとに進み具合を出す（止まったときにどこか分かるように）
                results.append(r)
                print(f"[{len(results)}/{len(todo)}] {r['k']} {r['when']} {'崩れた' if judge([r]) else 'ok'}", flush=True)
            results.sort(key=lambda r: (r["k"], r["when"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    broken = judge(results)
    print(f"書き込み {base_day.writes} 点＋モデルの控え {len(pkl)} 点・止めた {len(results)} 回・崩れた {len(broken)}")
    for r in broken[:40]:
        print(" 崩れた", r["k"], r["when"], r.get("died"), r.get("label"), sorted((r.get("diff") or {}).keys()),
              r.get("error", ""))
    if a.out:
        a.out.write_text(json.dumps({"base": base, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
