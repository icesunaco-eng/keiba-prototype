"""文章AI方式（まむ）とのやりとり。設計の3つ目の方式。

出す（export）：レース×時点ごとに、締切までに分かっていた情報だけの「入力の束」（JSON）を data/mam/outbox/ に書く。
  中身：レース条件、出走馬（取消を除く）、各馬の近4走・父・母の父・騎手・調教師、その時点の単勝・三連複オッズ（合格したときだけ）
  入れないもの：学習モデル・ルールの予想（独立させるため）、結果、締切後の情報
受け取る（import）：まむの予想（JSON。形は docs/requests/mam-format.md）を検証して、台帳に method="text_ai" で入れる
  - 返し先は data/mam/inbox/（outbox は入力の束の置き場なので、そこには置かない）。収集の見回りのたびに新しいファイルだけ読む
  - 同じレース・時点で受け付け済みなら、後から来た予想は断る（台帳は書き換えない）
  - 読んだ結果（受け付けた数・断った理由）は data/mam/inbox_results/<ファイル名> に書く。inbox のファイルは消さない・動かさない
  - 決めた形にない項目（例：trio_odds・win_prob）があれば、その予想は受け付けない（確率が黙って落ちるのを防ぐ）
  - 作成時刻（generated_at）が、その時点の締切より後なら受け付けない
  - 馬番は、その時点の出走馬（取消を除く）だけ
  - 三連複の確率があれば合計1に直して使う。無ければ順位から組を作るが、確率の裏づけが無いので配分は0円
  - 配分は学習モデルと同じ合格判定・同じ3配分。精算は wallet.run_live がまとめて行う
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from itertools import combinations
from pathlib import Path

from ..allocate import allocate
from ..ledger import Ledger, canonical, code_hash, sha
from .collector import deadline
from .wallet import ALLOC_METHODS, _snapshots, live_tables

MAM_VERSION = "live-mam-v1"
SCHEMA_IN = "keiba-mam-input-v1"
SCHEMA_OUT = "keiba-mam-prediction-v1"


def _state(root: Path) -> dict:
    return json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"races": {}}


def export_packets(paths) -> list[Path]:
    root = paths.data_dir / "live"
    state = _state(root)
    _, _, inputs = live_tables(root, state, _snapshots(root))
    out_dir = paths.data_dir / "mam" / "outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (rk, tp), inp in sorted(inputs.items()):
        if tp == "result":
            continue
        r = state["races"][rk]
        f = out_dir / f"{rk.replace('|', '_')}_{tp}.json"
        if f.exists():
            continue
        gate = inp["gate"]
        packet = {
            "schema": SCHEMA_IN, "race_key": rk, "time_point": tp,
            "deadline": deadline(r["race_date"], r["post_time"], tp).isoformat(timespec="minutes"),
            "race": {k: r.get(k) for k in ("race_date", "venue", "race_number", "post_time", "surface", "distance_m", "field_size", "race_name")},
            "scratched_known": inp["scratched"],
            "horses": [{k: c.get(k) for k in ("horse_number", "waku", "horse_name", "sex", "age", "weight_carried",
                                              "jockey_name", "trainer_name", "sire", "dam", "damsire", "past_runs")}
                       for c in inp["card"]],
            "win_odds": inp["win"]["odds"] if inp["win"]["ok"] else None,
            "trio_odds": inp["trio"]["odds"] if gate.get("pass") else None,
            "odds_time": gate.get("odds_time") if gate.get("pass") else None,
            "source_snapshots": inp["snapshots"],
            "note": "締切までに分かっていた情報だけ。予想は締切までに返すこと（形は mam-format.md）",
        }
        f.write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(f)
    return written


class Rejected(Exception):
    pass


ALLOWED_KEYS = {"schema", "race_key", "time_point", "generated_at", "author", "ranking", "trios", "confidence", "comment"}


def _validate(pred: dict, state: dict, inputs: dict) -> tuple[dict, list]:
    if not isinstance(pred, dict):
        raise Rejected("予想が JSON のオブジェクトでない")
    if pred.get("schema") == SCHEMA_IN:
        raise Rejected("これは入力の束（outbox 用）。予想は keiba-mam-prediction-v1 の形で返す")
    if pred.get("schema") != SCHEMA_OUT:
        raise Rejected(f"schema が {SCHEMA_OUT} でない")
    extra = sorted(set(pred) - ALLOWED_KEYS)
    if extra:
        raise Rejected(f"決めた形にない項目がある：{', '.join(extra)}（使える項目は mam-format.md の2章）")
    for t in pred.get("trios", []):
        if not isinstance(t, dict) or set(t) - {"combo", "p"} or "combo" not in t:
            raise Rejected("trios の各要素は {\"combo\": \"1-2-3\", \"p\": 0.1} の形にする")
    rk, tp = pred.get("race_key"), pred.get("time_point")
    if rk not in state["races"] or (rk, tp) not in inputs:
        raise Rejected(f"知らないレースか時点：{rk} {tp}")
    r = state["races"][rk]
    dl = deadline(r["race_date"], r["post_time"], tp)
    try:
        gen = datetime.fromisoformat(pred["generated_at"])
    except (KeyError, ValueError):
        raise Rejected("generated_at（タイムゾーン付き）が無い・読めない")
    if gen.tzinfo is None:
        raise Rejected("generated_at にタイムゾーンが無い")
    if gen > dl:
        raise Rejected(f"締切後の予想（締切 {dl:%H:%M}・作成 {gen.astimezone(dl.tzinfo):%H:%M}）")
    nums = {c["horse_number"] for c in inputs[(rk, tp)]["card"]}
    ranking = [int(x) for x in pred.get("ranking", [])]
    if ranking and (len(set(ranking)) != len(ranking) or not set(ranking) <= nums):
        raise Rejected("ranking の馬番が出走馬と合わない・重複")
    trios = []
    for t in pred.get("trios", []):
        combo = sorted(int(x) for x in t["combo"].split("-"))
        if len(set(combo)) != 3 or not set(combo) <= nums:
            raise Rejected(f"三連複の組が出走馬と合わない：{t['combo']}")
        trios.append(("-".join(map(str, combo)), float(t["p"]) if t.get("p") is not None else None))
    if not ranking and not trios:
        raise Rejected("ranking か trios のどちらかが要る")
    return {"race": r, "nums": nums, "ranking": ranking, "trios": trios, "gen": gen}, sorted(nums)


def import_predictions(paths, file: Path, _ctx=None) -> dict:
    root = paths.data_dir / "live"
    state, inputs = _ctx or _load(root)
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    preds = data if isinstance(data, list) else [data]
    led = Ledger(paths.ledger_path)
    ok, rejected = 0, []
    for pred in preds:
        try:
            v, nums = _validate(pred, state, inputs)
        except Rejected as exc:
            rejected.append({"race_key": pred.get("race_key"), "time_point": pred.get("time_point"), "reason": str(exc)})
            continue
        rk, tp = pred["race_key"], pred["time_point"]
        probs = {c: p for c, p in v["trios"] if p is not None}
        calibrated = bool(probs)
        if probs:
            total = sum(probs.values())
            probs = {c: p / total for c, p in probs.items()}
            ranked = sorted(probs, key=probs.get, reverse=True)
        else:
            order = v["ranking"] + [n for n in nums if n not in v["ranking"]]
            pos = {n: i for i, n in enumerate(order)}
            ranked = sorted(("-".join(map(str, c)) for c in combinations(sorted(nums), 3)),
                            key=lambda c: sum(pos[int(x)] for x in c.split("-")))
        gate = inputs[(rk, tp)]["gate"]
        odds = inputs[(rk, tp)]["trio"]["odds"] if gate.get("pass") else None
        payload = {
            "race_date": v["race"]["race_date"], "method_kind": "text_ai", "uses_post_race_odds": False,
            "horses": [{"n": n, "rank": (v["ranking"].index(n) + 1) if n in v["ranking"] else None} for n in nums],
            "trios": [{"combo": c, "p": round(probs[c], 6) if c in probs else None} for c in ranked[:20]],
            "trios_all": {c: round(p, 7) for c, p in probs.items()},
            "complete": bool(probs) and set(probs) == {"-".join(map(str, c)) for c in combinations(sorted(nums), 3)},
            "confidence": pred.get("confidence", "Z"),
            "allocation": {a: (lambda x: {"status": x.status if gate.get("pass") else "not_allocated_gate", "stakes": x.stakes,
                                          "total": x.total})(allocate(probs, odds, a, calibrated=calibrated)) for a in ALLOC_METHODS},
            "odds_gate": gate, "notes": [pred.get("comment", "")][:1], "generated_at": pred["generated_at"],
            "period": "live", "submitted_by": pred.get("author", "mam"),
        }
        added = led.add_prediction(rk, tp, "text_ai", MAM_VERSION, payload, sha(canonical(pred))[:16], "mam", code_hash())
        ok += int(added)
        if not added:
            rejected.append({"race_key": rk, "time_point": tp, "reason": "同じレース・時点の予想を受け付け済み（台帳は書き換えないので、先の予想が残る）"})
    led.commit()
    led.close()
    return {"accepted": ok, "rejected": rejected}


def _load(root: Path):
    state = _state(root)
    _, _, inputs = live_tables(root, state, _snapshots(root))
    return state, inputs


def import_inbox(paths) -> dict:
    """data/mam/inbox/*.json のうち、まだ読んでいない中身（SHA-256）だけ取り込む。
    書きかけで読めないファイルは「読めない」と記録し、中身が変われば次の見回りで読み直す。"""
    inbox = paths.data_dir / "mam" / "inbox"
    results = paths.data_dir / "mam" / "inbox_results"
    if not inbox.exists():
        return {"files": 0}
    index_path = results / "processed.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    todo = []
    for f in sorted(inbox.glob("*.json")):
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if index.get(f.name, {}).get("sha256") != digest:
            todo.append((f, digest))
    if not todo:
        return {"files": 0}
    results.mkdir(parents=True, exist_ok=True)
    ctx = _load(paths.data_dir / "live")
    summary = {"files": len(todo), "accepted": 0, "rejected": 0, "unreadable": 0}
    for f, digest in todo:
        try:
            out = import_predictions(paths, f, ctx)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            out = {"accepted": 0, "rejected": [], "unreadable": f"JSON として読めない：{exc}"}
            summary["unreadable"] += 1
        summary["accepted"] += out["accepted"]
        summary["rejected"] += len(out["rejected"])
        rec = {"file": f.name, "sha256": digest, "read_at": datetime.now().astimezone().isoformat(timespec="seconds"), **out}
        (results / f.name).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        index[f.name] = {"sha256": digest, "read_at": rec["read_at"]}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
