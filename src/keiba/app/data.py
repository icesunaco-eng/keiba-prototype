"""確認アプリが見るデータ（読むだけ）。

2つの見方がある：
  live   これからのレース（収集の state.json と、台帳の版 live-* の予想・精算）
  check  検証データ（最新の評価の封印期間。過去のレースを、画面の確認のために表示する。発走前の予想ではない）
台帳は読取専用で開く。予想を作り直したり書き換えたりはしない。
"""
from __future__ import annotations

import json
import re
import os
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

from ..settle import result_problem, valid_size, winning_combos  # noqa: F401（winning_combos は画面側からも使う）

TIME_POINTS = ("24h", "1h", "15m")
TP_LABEL = {"24h": "24時間前", "1h": "1時間前", "15m": "15分前"}
MODES = {"live": "これからのレース", "check": "検証データ（過去）"}

# 画面のタブ（キー → 表示名・台帳の方式。左が優先）
METHOD_TABS = {
    "ml": ("学習", ("ml_lgbm",)),
    "rule": ("ルール", ("rule",)),
    "mam": ("まむ", ("text_ai",)),
    "int": ("統合", ("int_market_ml", "int_market_ml_mam", "int_market")),
}
METHOD_LABEL = {
    "ml_lgbm": "学習", "ml_market": "学習＋単勝オッズ", "rule": "ルール v1.0", "rule_v11": "ルール 案v1.1",
    "rule_v01": "ルール 暫定v0.1", "text_ai": "まむ", "int_market": "市場のみ（基準）",
    "int_market_ml": "統合：市場＋学習", "int_market_ml_mam": "統合：市場＋学習＋まむ",
    "base_pop": "基準：人気順（確定オッズ）", "base_logit": "比較：ロジスティック",
}
ALLOC_LABEL = {"ev_weighted": "期待値", "equal_payout": "均等払戻", "kelly": "ケリー"}
ALLOC_STATUS = {
    "allocated": "配分あり", "zero_no_value": "見送り（期待値が1を超える組が無い）",
    "zero_no_probability": "見送り（確率の裏づけが足りない）", "unallocated_no_odds": "未配分（オッズ未発売）",
    "not_allocated_gate": "未配分（オッズの確認が不合格）", "not_allocated": "未配分（入力が足りない）",
    "not_allocated_role_a": "配分しない（ルールは説明・確認役）",
}
# 方式ごとに、仮想の財布の成績で主に見る配分
MAIN_WALLET = {"live": "ev_weighted", "check": "ref_top10_equal"}
WALLET_LABEL = {**ALLOC_LABEL, "ref_top10_equal": "参考：上位10組を100円ずつ", "ref_top5_equal": "参考：上位5組を100円ずつ"}
MARKS = ("◎", "○", "★")
LEDGER_TIMEOUT = 15   # 収集が台帳を書いている間、読むのを待つ秒数
SCRATCH_VALUES = (None, "取消", "除外")   # 出馬表の取消の欄（jra.parse_card）がとりうる値


def distance_band(d) -> str:
    if d is None or pd.isna(d):
        return "不明"
    d = int(d)
    return "短距離（〜1400m）" if d <= 1400 else "マイル・中距離（〜2000m）" if d <= 2000 else "長距離（2100m〜）"


def horse_probs(p: dict) -> dict[int, float]:
    """馬ごとの3着内の確率。方式によって持ち方が違うので、組の確率から足し上げる。"""
    if p.get("horses") and p["horses"][0].get("p_top3") is not None:
        return {int(h["n"]): float(h["p_top3"]) for h in p["horses"]}
    full = p.get("trio_probs_full") or p.get("trios_all")
    pairs = full.items() if full else [(t["combo"], t["p"]) for t in p.get("trios", []) if t.get("p") is not None]
    out: dict[int, float] = defaultdict(float)
    for combo, prob in pairs:
        for n in combo.split("-"):
            out[int(n)] += prob or 0.0
    return dict(out)


def marks_of(p: dict) -> list[tuple[str, int]]:
    hp = horse_probs(p)
    if not hp:  # 確率が無く順位だけの予想（まむ）は、順位の上から印にする
        ranked = sorted((h for h in p.get("horses") or [] if h.get("rank")), key=lambda h: h["rank"])
        return list(zip(MARKS, [int(h["n"]) for h in ranked]))
    top = sorted(hp, key=lambda n: (-hp[n], n))[:3]
    return list(zip(MARKS, top))


def allocation_of(p: dict, alloc: str) -> dict:
    a = p.get("allocation") or {}
    if alloc in a and isinstance(a[alloc], dict):
        return a[alloc]
    if "status" in a:  # 検証の予想（配分は1つだけ）
        return a
    return {"status": "unallocated_no_odds", "stakes": {}, "total": 0}


def is_skip(p: dict, alloc: str) -> bool:
    return allocation_of(p, alloc).get("status") in ("zero_no_value", "zero_no_probability")


def ro_connect(path) -> sqlite3.Connection:
    """読取専用で開く。ファイル名に ? や # があっても別のファイルを作らないように、正しい file: URI にする。"""
    # 収集が台帳に書いている間は少し待つ（すぐ「database is locked」にしない）
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=LEDGER_TIMEOUT)


def make_result(finished: list, finishers: int, scratched: set, payouts: dict, rows: int | None = None,
                field_size=None, numbers: list | None = None) -> dict:
    """結果1レース分。finished：（馬番, 着順）。同着で3着内が4頭以上なら actual は無く、payouts に的中組が複数入る。

    winners は着順から出した的中組。払戻にその組が欠けていれば payouts_missing に入れる（その組は払戻待ち）。
    complete が False なら結果を一部しか読めていない（incomplete に理由）。精算は保留する（不成立の返還にはしない）。
    rows：読めた結果の行数（取消・除外も含む）。field_size：予定の頭数。
    """
    top = sorted(int(n) for n, pos in finished if pos and pos <= 3)
    actual = "-".join(map(str, top)) if len(top) == 3 else None
    payouts = {str(c): int(v) for c, v in (payouts or {}).items()}
    winners = winning_combos(finished)
    missing = sorted(winners - set(payouts)) if payouts else []
    if actual is None and len(payouts) == 1:
        actual = next(iter(payouts))
    label = actual or ("・".join(sorted(payouts)) if payouts else None)
    incomplete = result_problem(finished, finishers, scratched, payouts, rows, field_size, numbers)
    return {"actual": actual, "label": label, "payout": payouts.get(actual) if actual else None,
            "payouts": payouts, "finishers": int(finishers), "scratched": set(scratched), "has_result": True,
            "winners": winners, "payouts_missing": missing, "complete": incomplete is None, "incomplete": incomplete,
            "finished": [(int(n), int(pos) if pos else None) for n, pos in finished]}


def card_post_time(page: str) -> str | None:
    """出馬表の原本から発走時刻（"発走時刻： 9時45分"）を読む。読めなければ None。"""
    import re
    try:   # 実物は「発走時刻：<strong>9時45分</strong>」のようにタグをまたぐので、表示の文字で読む
        from lxml import html as lxml_html
        text = lxml_html.fromstring(page).text_content() if page else ""
    except Exception:
        text = re.sub(r"<[^>]+>", "", page or "")
    m = re.search(r"発走時刻[：:]\s*(\d{1,2})時(\d{1,2})分", text)
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}" if m else None


def card_problem(card: list, field_size=None) -> str | None:
    """出馬表（jra.parse_card）を取消・馬番の照合に使えない理由。使えれば None。

    取消の欄は None（取消なし）か "取消"／"除外"。馬番が読めない行・欄が無い行・決まっていない値の行、
    馬番の重複や欠け（1〜頭数がそろっていない）、予定の頭数と行数のちがいがあれば「取消なし」とは言えない。
    """
    if not card:
        return "出馬表を読めない"
    nums = [c.get("horse_number") for c in card]
    if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in nums):
        return "馬番を読めない行がある"
    if not all("scratch" in c and c["scratch"] in SCRATCH_VALUES for c in card):
        return "取消の欄を読めない行がある"
    if len(set(nums)) != len(nums):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        return "馬番が重複している：" + "、".join(f"{n}番" for n in dup)
    try:
        size = int(field_size) if field_size is not None else None
    except (TypeError, ValueError):
        size = None
    expect = set(range(1, max(size or 0, max(nums)) + 1))
    if size and len(nums) > size:
        return f"予定の{size}頭より多い{len(nums)}頭の行がある（{max(nums)}番まで）"
    lack = sorted(expect - set(nums))
    if lack:
        return f"{len(expect)}頭のうち " + "、".join(f"{n}番" for n in lack) + " の行が無い"
    return None


def race_sort_key(src, rk: str) -> tuple:
    """日付 → 発走時刻 → 場 → レース番号（数字として）。race_key の文字列順だと 12R が 9R より前になる。"""
    r = (src.races.get(rk) if src is not None else None) or {}
    parts = rk.split("|")
    try:
        num = int(parts[2])
    except (IndexError, ValueError):
        num = 0
    return (parts[0], r.get("post_time") or "", parts[1] if len(parts) > 1 else "", num)


def probs_complete(p: dict) -> bool:
    """馬ごとの確率が「全部の組」から出ているか（上位の組だけ・順位だけなら False＝目安）。"""
    if p.get("horses") and p["horses"][0].get("p_top3") is not None:
        return True
    if p.get("trio_probs_full"):
        return True
    if p.get("trios_all"):
        return bool(p.get("complete"))
    return False


class Source:
    """1つの見方のデータ。台帳と表のファイルが変わったら読み直す。"""

    def __init__(self, paths, mode: str):
        self.paths, self.mode = paths, mode
        self.races: dict[str, dict] = {}
        self.preds: dict[str, dict] = defaultdict(dict)     # race_key → {(method, tp): payload}
        self.names: dict[str, dict] = defaultdict(dict)     # race_key → {馬番: 馬名}
        self.results: dict[str, dict] = {}                  # race_key → {actual, payout, payouts, finishers, scratched}
        self.scratched_now: dict[str, set] = {}             # race_key → 今わかっている取消・除外（予想を保存した後の分も）。読めなければ入れない
        self.card_checked_at: dict[str, str] = {}           # race_key → 取消を照合した出馬表の取得時刻
        self.card_problem: dict[str, str] = {}              # race_key → 出馬表を照合に使えなかった理由
        self.race_meta: dict = {}
        self.past_keys: set[str] = set()                    # 収集の予定表（7日で整理）から外れた、過去14日のレース（原本の記録から）
        self.settlements: list[dict] = []
        self.versions: list[dict] = []
        self.version = None
        self.loaded_at = time.time()
        self.notes: list[str] = []
        self.stale: str | None = None   # 前に読んだデータを表示しているとき、その理由
        (self._load_live if mode == "live" else self._load_check)()

    # ---- 共通 ----
    def _ledger(self):
        lp = self.paths.ledger_path
        if not lp.exists():
            return None
        return ro_connect(lp)

    def _version_list(self, conn):
        rows = conn.execute("SELECT version, MIN(created_at), MAX(created_at), COUNT(*), COUNT(DISTINCT race_key) "
                            "FROM predictions GROUP BY version ORDER BY MIN(created_at)").fetchall()
        self.versions = [{"version": v, "first": a, "last": b, "predictions": n, "races": r} for v, a, b, n, r in rows]

    # ---- これからのレース ----
    def _load_live(self):
        root = self.paths.data_dir / "live"
        state_f = root / "state.json"
        state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {"races": {}}
        for rk, r in state.get("races", {}).items():
            self.races[rk] = {"race_key": rk, "race_date": r.get("race_date"), "venue": r.get("venue"),
                              "race_number": r.get("race_number"), "race_name": r.get("race_name") or "",
                              "post_time": r.get("post_time"), "surface": r.get("surface"),
                              "distance_m": r.get("distance_m"), "field_size": r.get("field_size"),
                              "going": None, "race_class": None}
        conn = self._ledger()
        if conn is not None:
            self._version_list(conn)
            latest: dict = {}
            for rk, tp, m, v, created, payload in conn.execute(
                    "SELECT race_key,time_point,method,version,created_at,payload FROM predictions "
                    "WHERE version LIKE 'live-%' OR method='text_ai'"):
                key = (rk, m, tp)
                if key not in latest or created > latest[key][0]:
                    latest[key] = (created, v, payload)
            for (rk, m, tp), (created, v, payload) in latest.items():
                self.preds[rk][(m, tp)] = {**json.loads(payload), "version": v, "_created_at": created}
            for rk, tp, m, v, w, payload in conn.execute(
                    "SELECT race_key,time_point,method,version,wallet,payload FROM settlements WHERE version LIKE 'live-%' "
                    "OR method='text_ai'"):
                self.settlements.append({"race_key": rk, "time_point": tp, "method": m, "version": v, "wallet": w,
                                         **json.loads(payload)})
            conn.close()
            live_versions = [v["version"] for v in self.versions if v["version"].startswith("live-")]
            self.version = live_versions[-1] if live_versions else None
        self.race_meta = self._race_archive(root)
        self._live_pages(root, state)

    def _live_pages(self, root: Path, state: dict):
        """集めた原本から馬名と結果を読む（読めなければ空のまま。画面は「不明」と出す）。"""
        if not (root / "snapshots.jsonl").exists():
            return
        try:
            from ..live import jra
            from ..live.wallet import _read, _snapshots
            snaps = _snapshots(root)
            # 収集は終わった開催を7日で予定表から外す。履歴と結果のために、原本の記録に残っているレースを拾う
            from datetime import date
            today = date.today().isoformat()
            self.past_keys = {k for k in set(snaps["race_key"]) if isinstance(k, str) and k not in self.races
                              and k[:10] <= today}
            meta = self.race_meta
            for rk in self.past_keys:   # 過去のレースとして、詳細・時点の変化・記録の画面でも開けるようにする（予定の一覧には出さない）
                d, venue, num = (rk.split("|") + ["", "", "0"])[:3]
                m = meta.get(rk) or {}   # 収集が予定表から外すときに残した記録（頭数・発走時刻）。読めた行の数から頭数を推測しない
                self.races[rk] = {"race_key": rk, "race_date": d, "venue": venue,
                                  "race_number": int(num) if num.isdigit() else 0, "race_name": m.get("race_name") or "",
                                  "post_time": m.get("post_time"), "surface": None, "distance_m": None,
                                  "field_size": m.get("field_size"), "going": None, "race_class": None, "archived": True}
            for rk in self.races:
                s = snaps[(snaps["race_key"] == rk) & (snaps["kind"] == "card")]
                if not len(s):
                    continue
                try:   # 原本ごとに読む。1件読めなくても、ほかのレースは止めない
                    page = _read(root, s.iloc[-1])
                    card = jra.parse_card(page)
                except Exception as exc:
                    self.card_problem[rk] = "出馬表の原本を読めない"
                    self.notes.append(f"{rk} の出馬表を読めない：{exc!r}"[:200])
                    continue
                for c in card:
                    if c.get("horse_number"):
                        self.names[rk][int(c["horse_number"])] = c.get("horse_name") or ""
                race = self.races[rk]
                problem = card_problem(card, race.get("field_size"))
                if race.get("archived"):   # 予定表から外れたレース：発走時刻は控え、無ければ出馬表の原本から読む
                    race["post_time"] = race["post_time"] or card_post_time(page)
                if problem:
                    self.card_problem[rk] = problem
                else:
                    self.scratched_now[rk] = {int(c["horse_number"]) for c in card if c["scratch"]}
                    self.card_checked_at[rk] = str(s.iloc[-1]["fetched_at"])
            results = {}
            for rk in self.races:   # 結果の原本はレースごとに読む。1件読めなくても、ほかのレースは止めない
                s = snaps[(snaps["race_key"] == rk) & (snaps["kind"] == "result")]
                if not len(s):
                    continue
                try:
                    results[rk] = jra.parse_result(_read(root, s.iloc[-1]))
                except Exception as exc:   # このレースだけ結果待ちのまま
                    self.notes.append(f"{rk} の結果を読めない：{exc!r}"[:200])
            for rk, res in results.items():
                rows = res["rows"]
                if not rows:   # 結果のページはあるが読めた行が無い：不成立とは決めず「結果待ち」のまま
                    continue
                scr = {int(r["horse_number"]) for r in rows if r["status"] in ("withdrawn", "excluded")}
                if rk in self.scratched_now:
                    self.scratched_now[rk] = self.scratched_now[rk] | scr
                race = self.races[rk]
                res_ = make_result(
                    [(r["horse_number"], r["finish_position"]) for r in rows if r["status"] == "finished"],
                    sum(r["status"] == "finished" for r in rows), scr,
                    {p["combo"]: p["payout_yen"] for p in res.get("trio_payouts", [])},
                    len(rows), race.get("field_size"), [r["horse_number"] for r in rows])
                if not valid_size(race.get("field_size")) and res_["complete"]:
                    # 予定の頭数を確かめられないレースは、行の欠けを見分けられないので保留
                    res_.update(complete=False, incomplete="予定の頭数を確かめられない（過去の原本）ので結果がそろっているか分からない")
                self.results[rk] = res_
            self._resettle_unsettled()
        except Exception as exc:  # 原本の読取の失敗で画面を止めない
            self.notes.append(f"原本の読取に失敗：{exc!r}"[:200])

    def _race_archive(self, root: Path) -> dict:
        """収集が予定表から外したレースの記録（races_archive.jsonl、追記だけ）を読む。確認アプリは書かない。

        1行ずつ中身を確かめ、おかしい行は使わない（そのレースは頭数が分からないので結果は保留になる）。同じレースは後の行を使う。
        """
        f = root / "races_archive.jsonl"
        out: dict = {}
        if not f.exists():
            return out
        bad = 0
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            self.notes.append(f"過去のレースの記録を読めない：{exc!r}"[:200])
            return out
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1
                continue
            rk = row.get("race_key") if isinstance(row, dict) else None
            if not isinstance(rk, str) or rk.count("|") != 2:
                bad += 1
                continue
            size = row.get("field_size")
            post = row.get("post_time")
            name = row.get("race_name")
            out[rk] = {"field_size": size if valid_size(size) else None,
                       "post_time": post if isinstance(post, str) and re.fullmatch(r"\d{2}:\d{2}", post) else None,
                       "race_name": name if isinstance(name, str) else ""}
            if not valid_size(size):
                bad += 1
        if bad:
            self.notes.append(f"過去のレースの記録に読めない行・おかしい値が {bad}件（そのレースは保留）")
        return out

    def _resettle_unsettled(self):
        """前の版が台帳に書いた「払戻待ち」の精算を、いま読めている完全な結果で計算し直して表示する（台帳は書き直さない）。"""
        from ..settle import missing_payouts, settle_trio
        todo = [i for i, s in enumerate(self.settlements) if s.get("status") == "unsettled_no_payout"]
        if not todo or not self.paths.ledger_path.exists():
            return
        conn = ro_connect(self.paths.ledger_path)
        try:
            for i in todo:
                s = self.settlements[i]
                res = self.results.get(s["race_key"])
                if not res or not res.get("complete") or not res.get("payouts"):
                    continue
                row = conn.execute("SELECT payload FROM predictions WHERE race_key=? AND time_point=? AND method=? AND version=? "
                                   "ORDER BY created_at DESC LIMIT 1",
                                   (s["race_key"], s["time_point"], s["method"], s["version"])).fetchone()
                if not row:
                    continue
                stakes = (allocation_of(json.loads(row[0]), s["wallet"]).get("stakes")) or {}
                fin = [(n, pos) for n, pos in res.get("finished", [])]
                if not stakes or missing_payouts(stakes, fin, res["scratched"], res["payouts"]):
                    continue
                st = settle_trio(stakes, res["finishers"], res["scratched"], res["payouts"])
                if st.status == "unsettled_no_payout":
                    continue
                self.settlements[i] = {**s, "status": st.status, "stake": st.stake, "returned": st.returned,
                                       "hits": st.hits, "recomputed": True}
        finally:
            conn.close()

    # ---- 検証データ ----
    def _load_check(self):
        conn = self._ledger()
        if conn is None:
            return
        self._version_list(conn)
        run = conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if not run:
            conn.close()
            return
        self.version = run[0]
        for rk, tp, m, payload in conn.execute(
                "SELECT race_key,time_point,method,payload FROM predictions WHERE version=? "
                "AND json_extract(payload,'$.period')='holdout'", (self.version,)):
            self.preds[rk][(m, tp)] = {**json.loads(payload), "version": self.version}
        for rk, tp, m, w, payload in conn.execute(
                "SELECT race_key,time_point,method,wallet,payload FROM settlements WHERE version=?", (self.version,)):
            if rk in self.preds:
                self.settlements.append({"race_key": rk, "time_point": tp, "method": m, "version": self.version,
                                         "wallet": w, **json.loads(payload)})
        conn.close()
        t = self.paths.tables_dir
        if (t / "races.parquet").exists():
            races = pd.read_parquet(t / "races.parquet")
            races = races[races["race_key"].isin(self.preds.keys())]
            races = races.astype(object).where(races.notna(), None)  # 欠損（NaN）は None にそろえる
            for r in races.to_dict("records"):
                self.races[r["race_key"]] = {
                    "race_key": r["race_key"], "race_date": str(r["race_date"])[:10], "venue": r["venue"],
                    "race_number": int(r["race_number"]), "race_name": "", "post_time": r.get("post_time"),
                    "surface": r.get("surface"), "distance_m": r.get("distance_m"),
                    "field_size": int(r["field_size"]) if r.get("field_size") is not None else None,
                    "going": r.get("going"), "race_class": r.get("race_class")}
        if (t / "runners.parquet").exists():
            run = pd.read_parquet(t / "runners.parquet", columns=["race_key", "horse_number", "horse_name", "finish_position", "status"])
            run = run[run["race_key"].isin(self.preds.keys())]
            pay: dict = defaultdict(dict)
            if (t / "payouts.parquet").exists():
                p = pd.read_parquet(t / "payouts.parquet")
                for r in p[p["bet_type"] == "trio"].itertuples():
                    pay[r.race_key][r.combo] = int(r.payout_yen)
            per: dict = defaultdict(lambda: {"fin": [], "n": 0, "scr": set(), "rows": 0, "nums": []})
            for r in run.itertuples():
                self.names[r.race_key][int(r.horse_number)] = r.horse_name or ""
                x = per[r.race_key]
                x["rows"] += 1
                x["nums"].append(int(r.horse_number))
                if r.status == "finished":
                    x["n"] += 1
                    x["fin"].append((int(r.horse_number), int(r.finish_position) if pd.notna(r.finish_position) else None))
                elif r.status in ("withdrawn", "excluded"):
                    x["scr"].add(int(r.horse_number))
            for rk, x in per.items():
                self.scratched_now[rk] = x["scr"]
                if x["n"] or pay.get(rk):
                    self.results[rk] = make_result(x["fin"], x["n"], x["scr"], pay.get(rk, {}), x["rows"],
                                                   (self.races.get(rk) or {}).get("field_size"), x["nums"])
        for rk in self.preds:
            self.races.setdefault(rk, {"race_key": rk, "race_date": rk.split("|")[0], "venue": rk.split("|")[1],
                                       "race_number": int(rk.split("|")[2]), "race_name": "", "post_time": None,
                                       "surface": None, "distance_m": None, "field_size": None, "going": None,
                                       "race_class": None})

    # ---- 画面から使う ----
    def days(self) -> list[str]:
        return sorted({r["race_date"] for r in self.races.values() if r.get("race_date") and not r.get("archived")})

    def races_on(self, day: str) -> list[dict]:
        return sorted((r for r in self.races.values() if r["race_date"] == day and not r.get("archived")),
                      key=lambda r: (r.get("post_time") or "99:99", r["venue"], r["race_number"]))

    def pick(self, race_key: str, tab: str, tp: str) -> tuple[str | None, dict | None]:
        """タブ（学習・ルール・まむ・統合）と時点で予想を1つ選ぶ。その時点に無ければ None。"""
        for m in METHOD_TABS[tab][1]:
            p = self.preds.get(race_key, {}).get((m, tp))
            if p:
                return m, p
        return None, None

    def latest_tp(self, race_key: str, tab: str = "ml") -> str | None:
        for tp in reversed(TIME_POINTS):
            if self.pick(race_key, tab, tp)[1]:
                return tp
        return None

    def name(self, race_key: str, n: int) -> str:
        return self.names.get(race_key, {}).get(int(n), "")


class LedgerBusy(Exception):
    """収集が台帳を書いている最中で、まだ一度も読めていない（少し待てば読める）。"""


class Cache:
    """見方ごとに Source を持ち、元のファイルが変わったら作り直す。"""

    def __init__(self, paths):
        self.paths = paths
        self._lock = threading.Lock()
        self._items: dict[str, tuple[tuple, Source]] = {}

    def _stamp(self, mode: str) -> tuple:
        files = [self.paths.ledger_path]
        if mode == "live":
            root = self.paths.data_dir / "live"
            files += [root / "state.json", root / "snapshots.jsonl", root / "races_archive.jsonl"]
        else:
            files += [self.paths.tables_dir / f for f in ("races.parquet", "runners.parquet", "payouts.parquet")]
        return tuple(os.stat(f).st_mtime_ns if f.exists() else 0 for f in files)

    def get(self, mode: str) -> Source:
        mode = mode if mode in MODES else "live"
        with self._lock:
            stamp = self._stamp(mode)
            item = self._items.get(mode)
            if item is None or item[0] != stamp:
                try:
                    item = (stamp, Source(self.paths, mode))
                except sqlite3.OperationalError as exc:
                    # 収集が台帳を書いている間は読めないことがある。前に読んだデータで表示を続け、次の表示で読み直す
                    if item is None:
                        raise LedgerBusy(str(exc)) from exc
                    item[1].stale = f"台帳の書込み中だったので、{time.strftime('%H:%M:%S', time.localtime(item[1].loaded_at))} に読んだデータを表示中"
                    return item[1]
                self._items[mode] = item
            return item[1]


def version_compare(ledger_path, va: str, vb: str, tp: str = "1h", wallet: str = "ref_top10_equal") -> dict:
    """2つの版の精算を、両方にあるレースだけでくらべる（読むだけ）。"""
    out = {"races": 0, "rows": [], "a": va, "b": vb, "tp": tp, "wallet": wallet}
    if not Path(ledger_path).exists() or not va or not vb:
        return out
    conn = ro_connect(ledger_path)
    agg: dict = defaultdict(dict)   # (version, method) → {race_key: (stake, returned, hit)}
    for v, m, rk, payload in conn.execute(
            "SELECT version,method,race_key,payload FROM settlements WHERE version IN (?,?) AND wallet=? AND time_point=?",
            (va, vb, wallet, tp)):
        s = json.loads(payload)
        if s.get("status") not in ("settled", "refunded_void"):
            continue   # 払戻待ちなどは比べない（共通のレースにも入れない）
        agg[(v, m)][rk] = (s.get("stake") or 0, s.get("returned") or 0, bool(s.get("hits")))
    conn.close()
    methods = sorted({m for (_, m) in agg}, key=lambda m: list(METHOD_LABEL).index(m) if m in METHOD_LABEL else 99)
    all_races = set()
    for m in methods:
        a, b = agg.get((va, m), {}), agg.get((vb, m), {})
        common = set(a) & set(b)
        if not common:
            continue
        all_races |= common

        def stat(d):
            st = sum(d[k][0] for k in common)
            rt = sum(d[k][1] for k in common)
            return {"hit": sum(d[k][2] for k in common), "stake": st, "returned": rt, "roi": rt / st if st else None}
        out["rows"].append({"method": m, "races": len(common), "a": stat(a), "b": stat(b)})
    out["races"] = len(all_races)
    return out
