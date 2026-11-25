"""これからのレースを、集めた発走前のページで予想し、配分して、レース後に精算する（仮想の財布。実購入はしない）。

  予想の時点：24h・1h・15m（出馬表は、その時点までに取った一番新しいもの）
  財布を持つ方式：ml_lgbm（学習モデル）・ml_market（学習＋その時点の単勝オッズ）
  ルール方式（v1.0 と比較版）：役割A（説明・確認役）。予想は比較のために台帳へ残すが、配分はしない。
    学習モデルの予想には、上位3頭のルール8項目の評価と「食い違い」の印を添える（配分には使わない）
  配分：equal_payout・ev_weighted・kelly。次の「オッズの合格判定」を全部満たしたときだけ：
    - 三連複の組が、出走する頭数から決まる数とちょうど一致（欠落なし）で、重複なし
    - オッズの発表時刻が読めて、取得時刻との差が STALE_MINUTES 分以内（古いオッズでない）
    - 取得時刻も発表時刻も、その時点の締切（1h＝発走60分前、15m＝発走15分前）以前
  出馬表・取消・除外も、その時点の締切までに取った出馬表に載っていたものだけ反映する
  精算：15m の配分を使う（無ければ 1h）
  台帳の版は "live-<コードのハッシュ>"。過去の検証の版とは混ぜない
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from .. import evaluate as ev
from ..allocate import allocate
from ..features.asof import build_features
from ..ledger import Ledger, canonical, code_hash, sha
from ..models import rule_v1
from ..models.trio import fit_calibration
from ..integrate import pool
from ..settle import settle_trio
from . import jra
from .collector import DEADLINE_MIN, deadline

WALLET_METHODS = ("ml_lgbm", "ml_market")
# あいす統合（別枠の参考予想）。重みは あいすの回答（2026-11-19）どおり固定。市場のみが比べる基準
INT_METHODS = {
    "int_market": {"market": 1.0},
    "int_market_ml": {"market": 1.0, "ml": 0.2},
    "int_market_ml_mam": {"market": 1.0, "ml": 0.2, "mam": 0.2},
}
INT_TIME_POINTS = ("1h", "15m")
RULE_METHODS = {"rule": "v1.0", **{f"rule_{v.replace('.', '').replace('-', '_')}": v for v in rule_v1.COMPARISON_VARIANTS}}
ALLOC_METHODS = ("equal_payout", "ev_weighted", "kelly")
TP_ORDER = {"24h": 0, "1h": 1, "15m": 2}
STALE_MINUTES = 15
JST = "+09:00"


def _snapshots(root: Path) -> pd.DataFrame:
    f = root / "snapshots.jsonl"
    if not f.exists():
        return pd.DataFrame(columns=["race_key", "kind", "time_point", "path", "sha256", "fetched_at"])
    rows, bad = [], 0
    for x in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if not x.strip():
            continue
        # 1行に複数の記録がつながっていることがある（前の書き込みが改行の前で切れ、次の行が続けて書かれた）。
        # 行の頭から順に JSON を読み、読めた記録は使う。読めない部分（切れた記録）だけ捨てる
        pos, dec = 0, json.JSONDecoder()
        while pos < len(x):
            while pos < len(x) and x[pos].isspace():
                pos += 1
            if pos >= len(x):
                break
            try:
                rec, end = dec.raw_decode(x, pos)
            except json.JSONDecodeError:
                nxt = x.find('{"race_key"', pos + 1)  # 切れた記録を飛ばして、次の記録の頭から読み直す
                bad += 1
                if nxt < 0:
                    break
                pos = nxt
                continue
            if isinstance(rec, dict):
                rows.append(rec)  # ページの無い記録（odds_unavailable など）も今までどおり残す
            else:
                bad += 1
            pos = end
    df = pd.DataFrame(rows, columns=None if rows else ["race_key", "kind", "time_point", "path", "sha256", "fetched_at"])
    df.attrs["bad_lines"] = bad
    return df


def _read(root: Path, rec) -> str:
    return (root / rec["path"]).read_text(encoding="utf-8")


def card_problem(nums: list[int], field_size) -> str | None:
    """出馬表の名簿が信用できるか。馬番は1から頭数まで欠けず・重ならず、レース一覧の頭数とも同じであること。
    頭数が分からないときも信用しない（末尾の欠けを見分けられないため）。名簿が信用できなければ、単勝・三連複とも使わない。
    取消の馬も名簿には残る（取消の印つき）ので、取消があってもこの決まりは変わらない。"""
    if any(n is None for n in nums):
        return "出馬表に馬番の読めない行がある"
    if len(nums) != len(set(nums)):
        return "出馬表の馬番が重複している"
    if sorted(nums) != list(range(1, len(nums) + 1)):
        return "出馬表の馬番が欠けている"
    if not field_size:
        return "レース一覧の頭数が分からない（出馬表の欠けを確かめられない）"
    if int(field_size) != len(nums):
        return f"出馬表の頭数がレース一覧と合わない（出馬表 {len(nums)}・一覧 {field_size}）"
    return None


def _with_card_issue(gate: dict, issue: str | None) -> dict:
    if issue:
        gate = dict(gate, card_issue=issue, reasons=[*gate.get("reasons", []), issue])
        gate["pass"] = False
    return gate


def time_reasons(odds_time, fetched_at: str, race: dict, tp: str, label: str = "") -> list[str]:
    """オッズのページの時刻の検査（三連複・単勝で同じ）。取得が締切後・発表時刻が不明・締切後・古い（取得との差）なら理由を返す。"""
    out = []
    fetched = datetime.fromisoformat(fetched_at)
    dl = deadline(race["race_date"], race["post_time"], tp)
    if fetched > dl:
        out.append(f"{label}締切後の取得（締切 {dl:%H:%M}・取得 {fetched:%H:%M}）")
    if not odds_time:
        out.append(f"{label}オッズの時刻が分からない")
        return out
    # 発表時刻には日付が無い。取得した日（日本時間）の時刻として読み、取得より後になるなら前の日の発表と見る
    # （前日に取る24時間前のオッズは、レース当日ではなく取得した日の発表。実物：11/22京都11Rを11/21 15:25に取得、発表15:24）
    fetched_jst = fetched.astimezone(dl.tzinfo)
    # 時計の進み（1分まで）は日付をまたいでも許す：前日・当日・翌日の候補のうち、取得+1分までで一番遅いものを使う
    base = datetime.fromisoformat(f"{fetched_jst.date()}T{odds_time}:00").replace(tzinfo=dl.tzinfo)
    t = max(c for c in (base + timedelta(days=k) for k in (-1, 0, 1)) if c - fetched <= timedelta(minutes=1))
    if t > dl:
        out.append(f"{label}締切後のオッズ（発表 {odds_time}）")
    if fetched - t > timedelta(minutes=STALE_MINUTES) or t - fetched > timedelta(minutes=1):
        out.append(f"{label}古いオッズ（発表 {odds_time}・取得 {fetched:%H:%M}）")
    return out


def odds_gate(trio: dict, trio_rec, race: dict, tp: str) -> dict:
    """三連複オッズの合格判定（週末の確認の物差し）。"""
    g = {"trio_found": trio.get("found"), "trio_expected": trio.get("expected"), "duplicates": trio.get("duplicates"),
         "unreadable": trio.get("unreadable"), "extra": trio.get("extra"), "missing": trio.get("missing"),
         "no_votes": trio.get("no_votes", []), "fetched_at": None, "odds_time": None, "snapshot_sha256": None}
    reasons = []
    if trio_rec is None:
        reasons.append("三連複オッズのページが無い")
    else:
        g["fetched_at"], g["snapshot_sha256"] = trio_rec["fetched_at"], trio_rec["sha256"]
        g["odds_time"] = trio.get("odds_time")
        if trio.get("implausible"):  # 組のずれ・重複もあれば、理由は両方残す
            reasons.append(f"票数なし{len(trio['no_votes'])}組を除くと倍率の逆数の合計が1未満（倍率が欠けている）")
        if not trio.get("ok") and not (trio.get("implausible") and trio.get("complete")):
            reasons.append(f"組が出走馬の全組と合わない（{trio.get('found')}/{trio.get('expected')}・重複{trio.get('duplicates')}"
                           f"・余分{trio.get('extra', 0)}・不足{trio.get('missing', 0)}）")
        dl = deadline(race["race_date"], race["post_time"], tp)
        g["deadline"] = dl.isoformat(timespec="minutes")
        reasons += time_reasons(g["odds_time"], trio_rec["fetched_at"], race, tp)
    g["pass"] = not reasons
    g["reasons"] = reasons
    return g


def _write_held(root: Path, held: dict):
    """読み取りで保留にしたレースと理由（data/live/held.json）。毎回書き直す（今の状態だけ）。"""
    try:
        (root / "held.json").write_text(json.dumps(held, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def live_tables(root: Path, state: dict, snaps: pd.DataFrame, held: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """集めたページから、正規の表と同じ形の races / runners と、時点ごとの入力を作る。
    held に辞書を渡すと、読み取りで保留にしたレースと理由を入れて返す（ファイルには書かない）。"""
    races, runners, inputs = [], [], {}
    held = {} if held is None else held
    for rk, r in state.get("races", {}).items():
        s = snaps[snaps["race_key"] == rk] if len(snaps) else snaps
        races.append({"race_key": rk, "race_date": r["race_date"], "venue": r["venue"], "race_number": r["race_number"],
                      "surface": r.get("surface"), "distance_m": r.get("distance_m"), "going": None, "race_class": None,
                      "field_size": r.get("field_size"), "attr_source": "jra_live",
                      "race_type": "障害候補" if r.get("surface") == "障害" else "平地候補"})
        try:  # 1レースの読み取りの失敗で、ほかのレースを止めない（そのレースだけ保留にして理由を残す）
            for tp in ("24h", "1h", "15m"):
                if not r.get("post_time"):
                    continue
                dl = deadline(r["race_date"], r["post_time"], tp)
                before = s["fetched_at"].map(lambda x: datetime.fromisoformat(x) <= dl) if len(s) else s
                cards = s[(s["kind"] == "card") & before] if len(s) else s
                if not len(cards):
                    continue
                card_all = jra.parse_card(_read(root, cards.iloc[-1]))
                if getattr(card_all, "problems", None):
                    held[f"{rk}|{tp}"] = "出馬表を使わない：" + "・".join(card_all.problems)
                    continue
                if not any(c["horse_number"] for c in card_all):
                    # 枠順の確定前（馬番が空）。この時点の入力は作らない。
                    # ただし、この時点のオッズのページがあるのに馬番が全部読めないのは確定前ではないので、理由を残す
                    if len(s) and (s["kind"].isin(["odds_win", "odds_first", "odds_trio"]) & (s["time_point"] == tp)).any():
                        held[f"{rk}|{tp}"] = "オッズのページはあるのに、出馬表の馬番が1つも読めない"
                    continue
                scratched = sorted(c["horse_number"] for c in card_all if c.get("scratch") and c["horse_number"] is not None)
                # 番号の読めない行は出走馬に入れない（名簿の検査で不合格にして、理由を残す。例外で止めない）
                card = [c for c in card_all if not c.get("scratch") and c["horse_number"] is not None]
                if len(card) < 3:
                    held[f"{rk}|{tp}"] = f"出走馬（番号の読める・取消でない馬）が{len(card)}頭で、三連複を作れない"
                    continue
                win = s[(s["kind"].isin(["odds_win", "odds_first"])) & (s["time_point"] == tp) & before]
                trio = s[(s["kind"] == "odds_trio") & (s["time_point"] == tp)]  # 締切後のものも判定に出して不合格にする
                if tp == "15m" and not (len(win) or len(trio)):
                    continue  # 15分前は配分の見直しだけ。新しいオッズが無ければ作らない
                active = [int(c["horse_number"]) for c in card]  # 出走馬（取消を除く）。オッズの組はこれとぴったり照らす
                card_issue = ("・".join(getattr(card_all, "problems", [])) or
                              card_problem([c["horse_number"] for c in card_all], r.get("field_size")))
                w = {"ok": False, "odds": {}}
                if len(win):
                    win_page = _read(root, win.iloc[-1])
                    w = jra.parse_win_odds(win_page, active)
                    w["odds_time"] = jra.parse_odds_time(win_page)
                    w_time = time_reasons(w["odds_time"], win.iloc[-1]["fetched_at"], r, tp, "単勝：")
                    if w_time:  # 単勝も三連複と同じ時刻の検査。通らなければ使わない
                        w = dict(w, ok=False, reason=" / ".join(w_time))
                if card_issue:  # 名簿が信用できなければ単勝も使わない（学習＋単勝・統合へ誤った入力を渡さない）
                    w = {"ok": False, "odds": {}, "reason": card_issue}
                t = {"ok": False, "odds": {}}
                trio_rec = trio.iloc[-1] if len(trio) else None
                if trio_rec is not None:
                    page = _read(root, trio_rec)
                    t = jra.parse_trio_odds(page, active)
                    t["odds_time"] = jra.parse_odds_time(page)
                inputs[(rk, tp)] = {"card": card, "scratched": scratched, "win": w, "trio": t,
                                    "gate": _with_card_issue(odds_gate(t, trio_rec, r, tp) if tp != "24h" or trio_rec is not None else
                                                             {"pass": False, "reasons": ["前日はオッズを使わない（未発売のことが多い）"]},
                                                             card_issue),
                                    "card_fetched_at": cards.iloc[-1]["fetched_at"],
                                    "snapshots": [cards.iloc[-1]["sha256"], *win["sha256"].tolist()[-1:], *trio["sha256"].tolist()[-1:]]}
            res = s[s["kind"] == "result"] if len(s) else s
            if len(res):  # 結果が出たレースは確定行（過去走として使う）。発走前の入力（判定・予想用）はそのまま残す
                parsed = jra.parse_result(_read(root, res.iloc[-1]))
                cards = s[s["kind"] == "card"]
                card = {c["horse_number"]: c for c in jra.parse_card(_read(root, cards.iloc[-1]))} if len(cards) else {}
                for row in parsed["rows"]:
                    runners.append(_runner(rk, r, {**card.get(row["horse_number"], {}), **{k: v for k, v in row.items() if v is not None}}, row["status"]))
                inputs[(rk, "result")] = parsed
                continue
            last = max((tp for tp in ("24h", "1h", "15m") if (rk, tp) in inputs), key=TP_ORDER.get, default=None)
            if last:
                for c in inputs[(rk, last)]["card"]:
                    runners.append(_runner(rk, r, c, "pending"))
        except Exception as exc:
            for key in [k for k in inputs if k[0] == rk]:
                inputs.pop(key)
            runners[:] = [x for x in runners if x["race_key"] != rk]
            held[rk] = f"{type(exc).__name__}: {exc}"[:200]
    return pd.DataFrame(races), pd.DataFrame(runners), inputs


def _runner(rk, r, c, status):
    return {"runner_key": f"{rk}|{c['horse_number']}", "race_key": rk, "race_date": r["race_date"],
            "horse_number": c["horse_number"], "horse_id": c.get("horse_id"), "horse_name": c.get("horse_name"),
            "status": status, "counts_as_starter": status != "withdrawn", "finish_position": c.get("finish_position"),
            "dead_heat": False, "finish_time_s": c.get("finish_time_s"), "waku": c.get("waku"), "sex": c.get("sex"),
            "age": c.get("age"), "weight_carried": c.get("weight_carried"), "jockey_key": c.get("jockey_key"),
            "trainer_key": c.get("trainer_key"), "body_weight": c.get("body_weight"), "body_weight_diff": c.get("body_weight_diff"),
            "final_win_odds": None, "final_popularity": c.get("final_popularity"), "last3f_s": c.get("last3f_s"),
            "passage_raw": c.get("passage_raw"), "margin_raw": None, "attr_source": "jra_live", "venue": r["venue"],
            "race_number": r["race_number"], "owner": "live"}


def _models(paths, feat_hist: pd.DataFrame, tp: str, day: str):
    """学習モデルと市場組み合わせを、その日までのデータで1日1回学習してキャッシュする。ルールは学習しない（較正だけ）。"""
    cache = paths.data_dir / "live" / "models" / f"{day}-{tp}-{code_hash()[:8]}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cut = pd.Timestamp(day) - pd.Timedelta(days=ev.CALIB_DAYS)
    train, calib = feat_hist[feat_hist["race_date"] < cut], feat_hist[feat_hist["race_date"] >= cut]
    out = {}
    for m in WALLET_METHODS:
        model = ev.make_model(m, tp)
        model.fit(train)
        if hasattr(model, "fit_blend"):
            model.fit_blend(calib, ev._calibration_set)
        out[m] = (model, fit_calibration(ev._calibration_set(calib, model.score(calib))))
    for m, v in RULE_METHODS.items():
        model = rule_v1.RuleV1Model(tp, v)
        out[m] = (model, fit_calibration(ev._calibration_set(calib, model.score(calib))))
    cache.write_bytes(pickle.dumps(out))
    return out


def _features(paths, lr, lrun):
    races = pd.read_parquet(paths.tables_dir / "races.parquet")
    runners = pd.read_parquet(paths.tables_dir / "runners.parquet")
    lr = lr[~lr["race_key"].isin(races["race_key"])]
    lrun = lrun[~lrun["runner_key"].isin(runners["runner_key"])]
    all_races = pd.concat([races, lr], ignore_index=True)
    all_run = pd.concat([runners, lrun], ignore_index=True)
    feat = build_features(all_races, all_run)
    feat = feat.merge(rule_v1.build_utilities(all_races, all_run), on=["runner_key", "race_key"], how="left")
    for v in rule_v1.COMPARISON_VARIANTS:
        feat = feat.merge(rule_v1.build_utilities(all_races, all_run, v).drop(columns=["is_flat"]), on=["runner_key", "race_key"], how="left")
    pending = lrun.loc[lrun["status"] == "pending", "race_key"]
    return feat, ev.eligible_features(feat[~feat["race_key"].isin(pending)], all_races, 5)


def run_live(paths, log=print) -> dict:
    root = paths.data_dir / "live"
    state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"races": {}}
    snaps = _snapshots(root)
    held = {"_snapshots_bad_lines": snaps.attrs["bad_lines"]} if snaps.attrs.get("bad_lines") else {}
    lr, lrun, inputs = live_tables(root, state, snaps, held)
    _write_held(root, held)
    if lrun.empty:
        return {"predicted": 0, "settled": 0, "held": len(held)}
    feat, hist = _features(paths, lr, lrun)
    version = f"live-{code_hash()[:8]}"
    led = Ledger(paths.ledger_path)
    done = {(p["race_key"], p["time_point"], p["method"]) for p in led.predictions(version)}
    mam_preds = {(p["race_key"], p["time_point"]): p for p in led.predictions() if p["method"] == "text_ai"}
    n_pred = n_set = 0
    for (rk, tp), inp in sorted(inputs.items()):
        if tp == "result":
            continue
        g = feat[feat["race_key"] == rk].sort_values("horse_number")
        g = g[g["horse_number"].isin([c["horse_number"] for c in inp["card"]])]
        if len(g) < 3:
            continue
        g = g.assign(live_win_odds=g["horse_number"].map(inp["win"]["odds"]) if inp["win"]["ok"] else np.nan)
        models = _models(paths, hist, "1h" if tp == "15m" else tp, str(g["race_date"].iloc[0].date()))
        for m in (*WALLET_METHODS, *RULE_METHODS):
            if (rk, tp, m) in done:
                continue
            model, cal = models[m]
            if m == "ml_market":
                if not inp["win"]["ok"]:
                    continue
                model.odds_col = "live_win_odds"
            sc = np.asarray(model.score(g), dtype=float)
            if m in RULE_METHODS:
                v = RULE_METHODS[m]
                rg = rule_v1.score_frame(g, tp, rule_v1.VARIANTS[v]["prefix"])
                payload = ev.predict_race(g, sc, cal, "rule", tp, version, rg)
                payload["notes"] = [f"ルール {v}（役割A・比較用。配分しない）"]
                payload["allocation"] = {"status": "not_allocated_role_a"}
            else:
                payload = ev.predict_race(g, sc, cal, m, tp, version, None)
                nums = g["horse_number"].astype(int).tolist()
                probs = {"-".join(map(str, sorted(nums[i] for i in c))): p
                         for c, p in ev.trio_probs(cal.strength(sc), cal.gamma).items()}
                gate = inp["gate"]
                odds = inp["trio"]["odds"] if gate["pass"] else None
                payload["allocation"] = {a: (lambda x: {"status": x.status if gate["pass"] else "not_allocated_gate",
                                                        "stakes": x.stakes, "total": x.total})(
                    allocate(probs, odds, a, calibrated=cal.n_races >= 30)) for a in ALLOC_METHODS}
                payload["odds_gate"] = gate
                payload["trio_probs_full"] = {c: round(p, 7) for c, p in probs.items()}
            payload["inputs"] = {"snapshots": inp["snapshots"], "card_fetched_at": inp["card_fetched_at"],
                                 "scratched_known": inp["scratched"], "win_odds_ok": inp["win"]["ok"]}
            payload["period"] = "live"
            led.add_prediction(rk, tp, m, version, payload, sha(canonical(inp["snapshots"]))[:16],
                               sha(model.fingerprint() + canonical(cal.__dict__))[:16], code_hash())
            n_pred += 1
        # ---- あいす統合（別枠の参考予想）----
        if tp in INT_TIME_POINTS:
            n_pred += _integrated(led, rk, tp, inp, g, models, version, done, mam_preds, state)
    # 精算（財布を持つ方式だけ。まむ（text_ai）など、ほかの live 版の予想もまとめて精算する）
    preds = {}
    for p in led.predictions():
        if p["version"].startswith("live-") and isinstance(p.get("allocation"), dict) and "kelly" in p["allocation"]:
            preds[(p["race_key"], p["time_point"], p["method"], p["version"])] = p
    settled = {(s["race_key"], s["method"], s["version"], s["wallet"]) for s in led.settlements()}
    for (rk, tp), res in inputs.items():
        if tp != "result":
            continue
        rows = res["rows"]
        finishers = sum(r["status"] == "finished" for r in rows)
        scratched = {r["horse_number"] for r in rows if r["status"] in ("withdrawn", "excluded")}
        payouts = {p["combo"]: p["payout_yen"] for p in res["trio_payouts"]} or None
        for (m, ver) in sorted({(k[2], k[3]) for k in preds if k[0] == rk}):
            p = preds.get((rk, "15m", m, ver)) or preds.get((rk, "1h", m, ver))
            if not p:
                continue
            for a in ALLOC_METHODS:
                if (rk, m, ver, a) in settled:
                    continue
                stakes = p["allocation"][a]["stakes"]
                st = settle_trio(stakes, finishers, scratched, payouts) if stakes else None
                led.add_settlement(rk, p["time_point"], m, ver, a,
                                   {"status": st.status if st else p["allocation"][a]["status"],
                                    "stake": st.stake if st else 0, "returned": st.returned if st else 0,
                                    "hits": st.hits if st else []})
                n_set += 1
    led.commit()
    led.close()
    return {"predicted": n_pred, "settled": n_set, "version": version, "held": len(held)}


def market_probs(trio_odds: dict) -> dict:
    """発走前の三連複オッズ → 市場推定確率（1/オッズ を合計1に直したもの）。
    比較の基準には使うが、的中確率としての正確さは未確認。補正は入れない（8週点検で偏りが見えたら候補にする）。"""
    inv = {c: 1 / o for c, o in trio_odds.items() if o and o > 0}
    total = sum(inv.values())
    return {c: v / total for c, v in inv.items()}


def _mam_use(mp: dict, roster: list) -> tuple[bool, bool, str | None]:
    """保存済みのまむの予想を統合にどう使うか：(使う, 正式にしてよい, 理由)。
    取り決め（docs/requests/aisu-integration-reply.md）：正式は「同じ時点・同じ出走馬」で全組がそろったときだけ。
    同じ出走馬かは、まむに渡した入力の出走馬（issued_horses）と統合の時の出走馬（出馬表）で決める。
    全組がそろったかは確率の組から計算し、保存した印（complete）は真偽値の True であることだけを追加で確かめる。
    受付は合計1に直して7桁に丸めて保存する。"""
    probs = mp.get("trios_all")
    if probs is None or probs == {}:
        return True, False, "まむ：確率が無い（順位だけ）。正式な確率にしない（参考表示）"
    combos_now = {"-".join(map(str, c)) for c in combinations(sorted(roster), 3)}
    if not isinstance(probs, dict) or not any(probs.values()) or not all(
            c in combos_now and type(v) in (int, float) and (v == 0 or 1e-7 <= v <= 1) for c, v in probs.items()):
        return False, False, "まむ：保存済みの確率を使えない（数でない・範囲外・今の出走馬にない組）"
    total, tolerance = sum(probs.values()), len(probs) * 5e-8 + 1e-9
    if abs(total - 1) > tolerance:
        return False, False, f"まむ：保存済みの確率の合計が1でない（合計 {total:.12g}、許す幅は1±{tolerance:.2g}）"
    issued = mp.get("issued_horses")
    if not isinstance(issued, list) or any(type(n) is not int for n in issued):
        return True, False, "まむ：予想に使った入力の出走馬が分からない（古い版の受付・入力が読めない）。参考表示"
    if sorted(issued) != sorted(roster):
        return True, False, "まむ：予想に使った入力と今の出走馬が違う（同じ出走馬の予想ではないので参考表示）"
    if set(probs) != combos_now:
        return True, False, "まむ：全組の確率がそろっていない（足りない組を埋めて正式な確率にはしない。参考表示）"
    if mp.get("complete") is not True:  # 受付は真偽値で保存する。それ以外の値の行は手を加えられたものとして正式にしない
        return True, False, "まむ：保存した全組の印が真偽値の True でない（保存行の矛盾。参考表示）"
    return True, True, None


def _integrated(led, rk, tp, inp, g, models, version, done, mam_preds, state) -> int:
    """統合の3版を記録する。入力が欠けたら黙って外さず、理由を残して未配分にする。"""
    from datetime import timezone
    added = 0
    r = state["races"][rk]
    dl = deadline(r["race_date"], r["post_time"], tp)
    now = datetime.now(dl.tzinfo)
    gate = inp["gate"]
    nums = g["horse_number"].astype(int).tolist()
    model, cal = models["ml_lgbm"]
    ml = {"-".join(map(str, sorted(nums[i] for i in c))): p
          for c, p in ev.trio_probs(cal.strength(np.asarray(model.score(g), dtype=float)), cal.gamma).items()}
    roster = [c["horse_number"] for c in inp["card"]]
    combos_now = {"-".join(map(str, c)) for c in combinations(sorted(roster), 3)}
    for m, w in INT_METHODS.items():
        if (rk, tp, m) in done:
            continue
        reasons, sources, formal = [], {}, True
        if gate["pass"]:
            sources["market"] = market_probs(inp["trio"]["odds"])
            if inp["trio"].get("no_votes"):
                # 欠けた組の確率の埋め方は、あいすとの取り決めに無い。決まるまで統合は参考表示だけ（配分・成績の比較に入れない）
                formal = False
                reasons.append(f"市場推定確率：票数なしが{len(inp['trio']['no_votes'])}組あり、全組の確率がそろわない"
                               "（扱いが決まるまで正式な確率にしない。参考表示）")
        else:
            reasons.append("市場推定確率：三連複オッズが合格していない（" + " / ".join(gate.get("reasons", [])) + "）")
        if "ml" in w:
            sources["ml"] = ml
            if set(ml) != combos_now:  # 学習の出走馬は最新の時点の出馬表から作る。後の時点の取消が混ざると組がずれる
                formal = False
                reasons.append("学習：予想した出走馬が、この時点の出走馬と違う（後の時点の出馬表が混ざっている。参考表示）")
        if "mam" in w:
            mp = mam_preds.get((rk, tp))
            if mp is None:
                if now <= dl:
                    continue  # 締切まではまむの予想を待つ
                reasons.append("まむ：締切までに予想が無い")
            else:
                use, ok, why = _mam_use(mp, roster)
                if use:
                    sources["mam"] = mp.get("trios_all") or {}
                if not ok:
                    formal = False
                    reasons.append(why)
        missing = [k for k in w if k not in sources]
        if missing:
            formal = False
        pooled = pool(sources, w) if "market" in sources else {}
        allow = formal and gate["pass"] and bool(pooled)
        alloc = {}
        for a in ALLOC_METHODS:
            if allow:
                x = allocate(pooled, inp["trio"]["odds"], a, calibrated=True)
                alloc[a] = {"status": x.status, "stakes": x.stakes, "total": x.total}
            else:
                alloc[a] = {"status": "not_allocated", "stakes": {}, "total": 0, "reason": " / ".join(reasons) or "入力が足りない"}
        payload = {
            "race_date": str(g["race_date"].iloc[0].date()), "method_kind": "integrated", "uses_post_race_odds": False,
            "weights": w, "inputs_used": sorted(sources), "inputs_missing": missing, "formal_probability": formal and bool(pooled),
            "trios": [{"combo": c, "p": round(p, 6)} for c, p in list(pooled.items())[:20]],
            "trio_probs_full": {c: round(p, 7) for c, p in pooled.items()} if formal else None,
            "allocation": alloc, "odds_gate": gate, "reasons": reasons, "period": "live",
            "notes": ["あいす統合（別枠の参考予想）。ルールは入れない",
                      "市場推定確率＝発走前の三連複オッズの逆数を合計1に直したもの（的中確率としての正確さは未確認・補正なし）"],
            "horses": [{"n": n} for n in nums], "confidence": "Z" if not allow else "–",
        }
        led.add_prediction(rk, tp, m, version, payload, sha(canonical(inp["snapshots"]))[:16],
                           sha(canonical(w))[:16], code_hash())
        added += 1
    return added


def odds_check(paths) -> pd.DataFrame:
    """週末の合格判定：時点ごとに、三連複オッズを正しく読めたか（レースID・取得時刻・原本のハッシュつき）。"""
    root = paths.data_dir / "live"
    state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"races": {}}
    _, _, inputs = live_tables(root, state, _snapshots(root))
    rows = []
    for (rk, tp), inp in sorted(inputs.items()):
        if tp == "result":
            continue
        g = inp["gate"]
        rows.append({"race_key": rk, "time_point": tp, "deadline": g.get("deadline"), "pass": g["pass"], "reasons": " / ".join(g["reasons"]),
                     "trio": f"{g.get('trio_found')}/{g.get('trio_expected')}", "duplicates": g.get("duplicates"),
                     "no_votes": len(g.get("no_votes") or []),
                     "odds_time": g.get("odds_time"), "fetched_at": g.get("fetched_at"),
                     "scratched_known": ",".join(map(str, inp["scratched"])), "snapshot_sha256": g.get("snapshot_sha256")})
    return pd.DataFrame(rows)


def wallet_summary(paths) -> pd.DataFrame:
    led = Ledger(paths.ledger_path, read_only=True)
    rows = [s for s in led.settlements() if s["version"].startswith("live-")]
    led.close()
    if not rows:
        return pd.DataFrame(columns=["method", "wallet", "races", "bet_races", "stake", "returned", "roi"])
    df = pd.DataFrame(rows)
    out = df.groupby(["method", "wallet"]).agg(races=("race_key", "nunique"), bet_races=("stake", lambda s: int((s > 0).sum())),
                                               stake=("stake", "sum"), returned=("returned", "sum")).reset_index()
    out["roi"] = (out["returned"] / out["stake"].where(out["stake"] > 0)).round(4)
    out.attrs["note"] = "初週は回収率の達成判定をしない（あいすとの取り決め）。実購入はしない"
    return out
