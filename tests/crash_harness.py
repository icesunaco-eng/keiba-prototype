"""止める・重ねる試験の道具（💚20261125-21 のあと、たかむねの指示で作った）。

ふつうの見回りの入口（cli の collect tick）でレースの1日を流しながら、書き込み（ファイルの書き込み・置き換え・追記・
台帳の確定・まむの入力の置き場）が起きるたびに数え、k 番目でプロセスが死んだのと同じ状態を作る。
最後まで流したあと、止めなかった場合と比べて「崩れてはいけない決まり」を確かめる。
"""
from __future__ import annotations

import json
import os
import pickle
import sqlite3
from contextlib import contextmanager, redirect_stdout
from datetime import timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keiba import cli, official
from keiba.ledger import Ledger
from keiba.live import collector as collector_module, wallet
from keiba.live.collector import Collector
from keiba.live.jra import ENTRY_INDEX
from tests.test_capture_window_shift import LIST, RK, T, _pages, _paths
from tests.test_live_collector import INDEX, FakeClient, race_list
from tests.test_live_wallet import result_html

RESULT = "pw01sde1008202605061120261122/BD"
# 1日の見回り（本番と同じ5分ごと）：1時間前の時間帯（14:25〜14:40）に3回・締切の後の期限（〜14:50）に2回、
# 15分前の時間帯（15:10〜15:25）に3回・締切の後の期限（〜15:35）に2回、結果の後に3回
TICKS = ("14:00", "14:26", "14:31", "14:36", "14:41", "14:46", "15:11", "15:16", "15:21", "15:26", "15:31",
         "16:16", "16:21", "16:26")
# 途中で止まった見回りは「最後までやった1日」か「その見回りが丸ごと無かった1日」のどちらかと同じになるのが正しい
# （JRAのページは時刻で変わる：14:30から3番の取消。止まった回の分は次の回に、その時のページで取り直す）


class Died(BaseException):
    """プロセスが死んだ（電源・強制終了）。except Exception では捕まらない。"""


def _pages_at(clock):
    def fetch(cname):
        if cname == ENTRY_INDEX[1] or cname.startswith("pw01dli"):
            return INDEX
        pages = _pages(clock)
        if cname == LIST:
            return race_list(odds=True, result=clock["now"] >= T("16:16")).replace(
                "15時40分", "15時40分").replace("18頭", "8頭")
        if cname == RESULT:
            return result_html([1, 2, 4, 5, 6, 7, 8, 3]).replace(
                '<td class="place">8</td><td class="num">3</td>', '<td class="place">取消</td><td class="num">3</td>')
        return pages[cname]
    return fetch


class Writes:
    """書き込みを数え、crash_at 番目（1から）で Died を投げる。when は before（操作の前）／after（操作の後）。"""

    def __init__(self, crash_at: int | None = None, when: str = "before", probe=None):
        self.count, self.crash_at, self.when, self.log = 0, crash_at, when, []
        self.ledgers: list[Ledger] = []
        self.probe, self.inside = probe, False   # probe：書き込みの直前ごとに割り込む読み手・書き手（重なりの試験）

    def hit(self, what: str, do):
        if self.inside:   # 割り込んだ側の書き込みは数えない
            return do()
        self.count += 1
        self.log.append(what)
        if self.probe is not None:
            self.inside = True
            try:
                self.probe(self.count, what)
            finally:
                self.inside = False
        if self.crash_at == self.count and self.when == "before":
            raise Died(what)
        out = do()
        if self.crash_at == self.count and self.when == "after":
            raise Died(what)
        return out

    @contextmanager
    def watching(self):
        w = self
        real_wt, real_wb, real_rep = Path.write_text, Path.write_bytes, Path.replace
        real_link, real_append = os.link, collector_module.append_lines
        real_commit, real_init = Ledger.commit, Ledger.__init__

        def init(self, *a, **k):
            real_init(self, *a, **k)
            w.ledgers.append(self)

        with patch.object(Path, "write_text", lambda p, *a, **k: w.hit(f"write_text {p.name}", lambda: real_wt(p, *a, **k))), \
                patch.object(Path, "write_bytes", lambda p, *a, **k: w.hit(f"write_bytes {p.name}", lambda: real_wb(p, *a, **k))), \
                patch.object(Path, "replace", lambda p, t: w.hit(f"replace {Path(t).name}", lambda: real_rep(p, t))), \
                patch.object(os, "link", lambda s, d: w.hit(f"link {Path(d).name}", lambda: real_link(s, d))), \
                patch.object(collector_module, "append_lines",
                             lambda path, lines: w.hit(f"append {Path(path).name}", lambda: real_append(path, lines))), \
                patch.object(Ledger, "commit", lambda self: w.hit("ledger commit", lambda: real_commit(self))), \
                patch.object(Ledger, "__init__", init):
            yield self

    def bury(self):
        """死んだプロセスの台帳のつなぎを片付ける（確定していない書き込みは消える）。"""
        for led in self.ledgers:
            try:
                led.conn.rollback()
                led.conn.close()
            except sqlite3.Error:
                pass
        self.ledgers.clear()


def run_day(directory: Path, crash_at: int | None = None, when: str = "before", models: Path | None = None,
            probe=None, skip: str | None = None):
    """1日を流す。crash_at を渡すと、その番目の書き込みで1回だけ死に、次の見回りから続ける（その回は捨てる）。
    models：学習済みのモデルの控えを先に置く（毎回学習し直さないため。控えを書く途中も別に試す）。"""
    paths = _paths(directory)
    root = paths.data_dir / "live"
    if models is not None:
        dest = root / "models"
        dest.mkdir(parents=True, exist_ok=True)
        for f in models.glob("*.pkl"):
            (dest / f.name).write_bytes(f.read_bytes())
    clock = {"now": T("14:00")}
    client = FakeClient(_pages_at(clock))
    w = Writes(crash_at, when, probe(paths) if probe else None)
    died = []
    for hm in TICKS:
        if hm == skip:   # その見回りが丸ごと無かった1日（止まった見回りの比べる相手）
            continue
        clock["now"] = T(hm)
        out = StringIO()
        with patch.object(collector_module, "Collector",
                          lambda folder: Collector(folder, client=client, now=lambda: clock["now"])), \
                patch.object(Collector, "check_notices", lambda self, log=print: None), \
                patch.object(cli, "_notify", lambda *a, **k: None), \
                patch.object(wallet, "prediction_clock", lambda: clock["now"]), \
                patch.object(Ledger, "_now", lambda _: clock["now"].astimezone(timezone.utc).isoformat(timespec="seconds")), \
                redirect_stdout(out):
            try:
                with w.watching():
                    cli.cmd_collect(paths, SimpleNamespace(action="tick"))
            except Died as d:
                died.append((hm, str(d)))
                w.bury()
        w.crash_at = None if died else w.crash_at   # 死ぬのは1回だけ
    return SimpleNamespace(paths=paths, root=root, writes=w.count, log=w.log, died=died, probe=w.probe)


def invariants(day) -> dict:
    """崩れてはいけない決まりを、比べられる形で返す（止めなかった1日と同じであること）。"""
    from keiba import review
    paths, root = day.paths, day.root
    led = Ledger(paths.ledger_path, read_only=True)
    try:
        sel = official.load(led.conn, root)
        counted = sorted((rk, m, tp) for (rk, m), (tp, v) in sel.counted.items())
        stakes = sorted((p["race_key"], p["time_point"], p["method"], json.dumps(
            {a: (v or {}).get("stakes") for a, v in sorted((p.get("allocation") or {}).items()) if isinstance(v, dict)},
            sort_keys=True)) for p in led.predictions() if sel.is_counted(p["race_key"], p["time_point"], p["method"], p["version"]))
        settled = sorted((s["race_key"], s["time_point"], s["method"], s["wallet"], s.get("status"), s.get("stake"),
                          s.get("returned")) for s in led.settlements())
    finally:
        led.close()
    month = RK[:7]
    saved = paths.data_dir / "review" / f"{month}.json"
    review_ok = saved.exists() and json.loads(saved.read_text(encoding="utf-8"))["wallets"] == json.loads(
        json.dumps(review.build_review(paths, month), ensure_ascii=False, default=str))["wallets"]
    outbox = paths.data_dir / "mam" / "outbox"
    mam_files = sorted(f.name for f in outbox.glob("*.json")) if outbox.exists() else []
    mam_ok = all(json.loads(f.read_text(encoding="utf-8")).get("race_key") for f in outbox.glob("*.json")) if outbox.exists() else True
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    missed = sorted(json.loads((root / "health.json").read_text(encoding="utf-8")).get("missed", []))
    told = sorted(state.get("missed_notified", []))
    models_ok = all(pickle.loads(f.read_bytes()) is not None for f in (root / "models").glob("*.pkl")) \
        if (root / "models").exists() else True
    recs = official.capture_records(root)
    from keiba.live.collector import deadline
    taken_without_material = sorted(
        k for k in state.get("taken", {}) if not k.endswith("|result") and official.material_time(
            recs.get(tuple(k.rsplit("|", 1)), []), deadline(RK[:10], "15:40", k.rsplit("|", 1)[1])) is None)
    return {"counted": counted, "stakes": stakes, "settled": settled, "review_matches_ledger": review_ok,
            "stale_months": review.stale_months(paths), "mam_files": mam_files, "mam_json_complete": mam_ok,
            "models_loadable": models_ok, "taken_without_material": taken_without_material, "missed_told": told}


def readers(paths):
    """書き込みの直前ごとに、確認アプリ・CLI・月次が読む値を記録する読み手。読み手の失敗も記録する。"""
    from keiba import review
    from keiba.app.data import Source
    seen = []

    def probe(k, what):
        try:
            led = Ledger(paths.ledger_path, read_only=True) if paths.ledger_path.exists() else None
            counted = set()
            if led is not None:
                try:
                    sel = official.load(led.conn, paths.data_dir / "live")
                    counted = {(rk, m) for (rk, m) in sel.counted}
                finally:
                    led.close()
            w = wallet.wallet_summary(paths)
            money = {(r.method, r.wallet): (int(r.stake), int(r.returned)) for r in w.itertuples()} if len(w) else {}
            src = Source(paths, "live")
            shown = {(rk, m, tp) for rk, ps in src.preds.items() for (m, tp) in ps}
            month = {(r["method"], r["wallet"]): (int(r["stake"]), int(r["returned"]))
                     for r in review.build_review(paths, RK[:7])["wallets"]}
            seen.append({"k": k, "what": what, "counted": counted, "money": money, "shown": shown, "month": month})
        except Exception as exc:   # noqa: BLE001（読み手が落ちること自体を記録する）
            seen.append({"k": k, "what": what, "error": repr(exc)[:300]})
    probe.seen = seen
    return probe


def exporter(paths):
    """書き込みの直前ごとに、手で動かす mam export を割り込ませる書き手。"""
    from keiba.live import mam
    errors = []

    def probe(k, what):
        try:
            mam.export_packets(paths)
        except Exception as exc:   # noqa: BLE001
            errors.append((k, what, repr(exc)[:300]))
    probe.errors = errors
    return probe
