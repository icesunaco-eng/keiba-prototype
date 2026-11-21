"""文章AI方式（まむ）とのやりとり。設計の3つ目の方式。

出す（export）：レース×時点ごとに、締切までに分かっていた情報だけの「入力の束」（JSON）を data/mam/outbox/ に書く。
  中身：レース条件、出走馬（取消を除く）、各馬の近4走・父・母の父・騎手・調教師、その時点の単勝・三連複オッズ（合格したときだけ）
  入れないもの：学習モデル・ルールの予想（独立させるため）、結果、締切後の情報
受け取る（import）：まむの予想（JSON。形は docs/requests/mam-format.md）を検証して、台帳に method="text_ai" で入れる
  - 返し先は data/mam/inbox/（outbox は入力の束の置き場なので、そこには置かない）。収集の見回りのたびに新しいファイルだけ読む
  - 同じレース・時点で受け付け済みなら、後から来た予想は断る（台帳は書き換えない）
  - 読んだ結果（受け付けた数・断った理由）は data/mam/inbox_results/<ファイル名> に書く。inbox のファイルは消さない・動かさない
  - 届いた時刻（ファイルの更新時刻）が締切の MAM_GRACE_MIN 分後より後なら受け付けない
  - 決めた形にない項目（例：trio_odds・win_prob）があれば、その予想は受け付けない（確率が黙って落ちるのを防ぐ）
  - 作成時刻（generated_at）が、その時点の締切より後なら受け付けない
  - 馬番は、その時点の出走馬（取消を除く）だけ
  - 三連複の確率があれば合計1に直して使う。無ければ順位から組を作るが、確率の裏づけが無いので配分は0円
  - 配分は学習モデルと同じ合格判定・同じ3配分。精算は wallet.run_live がまとめて行う
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path

from ..allocate import allocate
from ..ledger import Ledger, LedgerConflict, canonical, code_hash, sha
from .collector import MAM_GRACE_MIN, deadline
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
CONFIDENCE = {"A", "B", "C", "D", "Z"}
INDEX_NAME = "inbox_state.json"  # inbox_results/ の外に置く（結果ファイル名とぶつからないように）


def _int(x, what):
    if isinstance(x, bool) or not isinstance(x, (int, str)) or not str(x).strip().isdigit():
        raise Rejected(f"{what} が整数でない：{x!r}")
    return int(x)


def _validate(pred, state: dict, inputs: dict, received_at: datetime | None = None) -> tuple[dict, list]:
    if not isinstance(pred, dict):
        raise Rejected("予想が JSON のオブジェクトでない")
    if pred.get("schema") == SCHEMA_IN:
        raise Rejected("これは入力の束（outbox 用）。予想は keiba-mam-prediction-v1 の形で返す")
    if pred.get("schema") != SCHEMA_OUT:
        raise Rejected(f"schema が {SCHEMA_OUT} でない")
    extra = sorted(set(pred) - ALLOWED_KEYS)
    if extra:
        raise Rejected(f"決めた形にない項目がある：{', '.join(extra)}（使える項目は mam-format.md の2章）")
    rk, tp = pred.get("race_key"), pred.get("time_point")
    if not isinstance(rk, str) or not isinstance(tp, str) or rk not in state["races"] or (rk, tp) not in inputs:
        raise Rejected(f"知らないレースか時点：{rk!r} {tp!r}")
    r = state["races"][rk]
    dl = deadline(r["race_date"], r["post_time"], tp)
    gen_raw = pred.get("generated_at")
    if not isinstance(gen_raw, str):
        raise Rejected("generated_at（タイムゾーン付きの文字列）が無い")
    try:
        gen = datetime.fromisoformat(gen_raw)
    except ValueError:
        raise Rejected(f"generated_at が読めない：{gen_raw!r}")
    if gen.tzinfo is None:
        raise Rejected("generated_at にタイムゾーンが無い")
    if gen > dl:
        raise Rejected(f"締切後の予想（締切 {dl:%H:%M}・作成 {gen.astimezone(dl.tzinfo):%H:%M}）")
    if received_at is not None and received_at > dl + timedelta(minutes=MAM_GRACE_MIN):
        raise Rejected(f"受付の締め（締切の{MAM_GRACE_MIN}分後）より後に届いた（届いた {received_at.astimezone(dl.tzinfo):%H:%M}）")
    for k in ("author", "comment"):
        if k in pred and pred[k] is not None and not isinstance(pred[k], str):
            raise Rejected(f"{k} は文字列にする")
    conf = pred.get("confidence", "Z")
    if conf not in CONFIDENCE:
        raise Rejected(f"confidence は A/B/C/D/Z のどれか：{conf!r}")
    nums = {int(c["horse_number"]) for c in inputs[(rk, tp)]["card"]}
    raw_rank = pred.get("ranking", [])
    if raw_rank is None:
        raw_rank = []
    if not isinstance(raw_rank, list):
        raise Rejected("ranking は馬番の配列にする")
    ranking = [_int(x, "ranking の馬番") for x in raw_rank]
    if len(set(ranking)) != len(ranking) or not set(ranking) <= nums:
        raise Rejected("ranking の馬番が出走馬と合わない・重複")
    raw_trios = pred.get("trios", [])
    if raw_trios is None:
        raw_trios = []
    if not isinstance(raw_trios, list):
        raise Rejected("trios は配列にする")
    trios, seen = [], set()
    for t in raw_trios:
        if not isinstance(t, dict) or set(t) - {"combo", "p"} or not isinstance(t.get("combo"), str):
            raise Rejected("trios の各要素は {\"combo\": \"1-2-3\", \"p\": 0.1} の形にする")
        parts = t["combo"].split("-")
        if len(parts) != 3:
            raise Rejected(f"三連複の組は3頭：{t['combo']}")
        combo = sorted(_int(x, "組の馬番") for x in parts)
        if len(set(combo)) != 3 or not set(combo) <= nums:
            raise Rejected(f"三連複の組が出走馬と合わない：{t['combo']}")
        key = "-".join(map(str, combo))
        if key in seen:
            raise Rejected(f"同じ組が2回ある：{key}")
        seen.add(key)
        p = t.get("p")
        if p is not None:
            if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
                raise Rejected(f"p は 0〜1 の数：{t['combo']} {p!r}")
            p = float(p)
        trios.append((key, p))
    if not ranking and not trios:
        raise Rejected("ranking か trios のどちらかが要る")
    if trios and all(p is not None for _, p in trios) and sum(p for _, p in trios) <= 0:
        raise Rejected("trios の p の合計が0")
    return {"race": r, "nums": nums, "ranking": ranking, "trios": trios, "gen": gen}, sorted(nums)


def _payload(pred, v, nums, inputs):
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
    return {
        "race_date": v["race"]["race_date"], "method_kind": "text_ai", "uses_post_race_odds": False,
        "horses": [{"n": n, "rank": (v["ranking"].index(n) + 1) if n in v["ranking"] else None} for n in nums],
        "trios": [{"combo": c, "p": round(probs[c], 6) if c in probs else None} for c in ranked[:20]],
        "trios_all": {c: round(p, 7) for c, p in probs.items()},
        "complete": bool(probs) and set(probs) == {"-".join(map(str, c)) for c in combinations(sorted(nums), 3)},
        "confidence": pred.get("confidence", "Z"),
        "allocation": {a: (lambda x: {"status": x.status if gate.get("pass") else "not_allocated_gate", "stakes": x.stakes,
                                      "total": x.total})(allocate(probs, odds, a, calibrated=calibrated)) for a in ALLOC_METHODS},
        "odds_gate": gate, "notes": [pred.get("comment") or ""][:1], "generated_at": pred["generated_at"],
        "period": "live", "submitted_by": pred.get("author") or "mam",
    }


def _import_data(paths, data, ctx, received_at) -> dict:
    """1件ずつ検証・記録する。1件の失敗（型の違いなど）で、ほかの予想を止めない。"""
    state, inputs = ctx
    preds = data if isinstance(data, list) else [data]
    led = Ledger(paths.ledger_path)
    ok, already, rejected = 0, 0, []
    try:
        for pred in preds:
            ident = {"race_key": pred.get("race_key") if isinstance(pred, dict) else None,
                     "time_point": pred.get("time_point") if isinstance(pred, dict) else None}
            try:
                v, nums = _validate(pred, state, inputs, received_at)
                payload = _payload(pred, v, nums, inputs)
                try:
                    added = led.add_prediction(pred["race_key"], pred["time_point"], "text_ai", MAM_VERSION, payload,
                                               sha(canonical(pred))[:16], "mam", code_hash())
                except LedgerConflict:
                    raise Rejected("同じレース・時点の予想を受け付け済み（台帳は書き換えないので、先の予想が残る）")
                led.commit()
                if added:
                    ok += 1
                else:
                    already += 1  # 同じ中身を受け付け済み（読み直し・結果の書き込みのやり直し）。受け付けた扱いのまま
            except Rejected as exc:
                rejected.append({**ident, "reason": str(exc)})
            except Exception as exc:  # 想定外でも、その1件だけ断って続ける
                rejected.append({**ident, "reason": f"検証できなかった（{type(exc).__name__}）"})
    finally:
        led.close()
    return {"accepted": ok, "already_accepted": already, "rejected": rejected}


def _received_at(f: Path) -> datetime:
    return datetime.fromtimestamp(f.stat().st_mtime).astimezone()


def import_predictions(paths, file: Path, _ctx=None) -> dict:
    """ファイルを1つ取り込む。届いた時刻はファイルの更新時刻で見る。"""
    f = Path(file)
    received = _received_at(f)
    data = json.loads(f.read_bytes().decode("utf-8"))
    return _import_data(paths, data, _ctx or _load(paths.data_dir / "live"), received)


def _load(root: Path):
    state = _state(root)
    _, _, inputs = live_tables(root, state, _snapshots(root))
    return state, inputs


def _write_atomic(path: Path, text: str):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def import_inbox(paths) -> dict:
    """data/mam/inbox/*.json のうち、まだ読んでいない中身（SHA-256）だけ取り込む。
    - 1回だけ読んだバイト列で、ハッシュ・解析・取り込みをする（読む途中で書き換わってもずれない）
    - 書きかけで読めないものは「読めない」と記録し、中身が変われば次の見回りで読み直す
    - 管理ファイル（data/mam/inbox_state.json）は1件ごとに置き換えで書く。壊れていたら作り直す（台帳が二重の受付を防ぐ）"""
    mam = paths.data_dir / "mam"
    inbox, results, index_path = mam / "inbox", mam / "inbox_results", mam / INDEX_NAME
    if not inbox.exists():
        return {"files": 0}
    try:
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        if not isinstance(index, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        index = {}  # 作り直す。読み直しても、同じ中身は台帳で「受け付け済み」になる
    todo = []
    for f in sorted(inbox.glob("*.json")):
        if not f.is_file():
            continue
        raw = f.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(index.get(f.name), dict) or index[f.name].get("sha256") != digest:
            todo.append((f, raw, digest, _received_at(f)))
    if not todo:
        return {"files": 0}
    results.mkdir(parents=True, exist_ok=True)
    ctx = _load(paths.data_dir / "live")
    summary = {"files": len(todo), "accepted": 0, "already_accepted": 0, "rejected": 0, "unreadable": 0}
    for f, raw, digest, received in todo:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            out = {"accepted": 0, "already_accepted": 0, "rejected": [], "unreadable": f"JSON として読めない：{exc}"}
            summary["unreadable"] += 1
        else:
            out = _import_data(paths, data, ctx, received)
        summary["accepted"] += out["accepted"]
        summary["already_accepted"] += out["already_accepted"]
        summary["rejected"] += len(out["rejected"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rec = {"file": f.name, "sha256": digest, "received_at": received.isoformat(timespec="seconds"), "read_at": now, **out}
        _write_atomic(results / f.name, json.dumps(rec, ensure_ascii=False, indent=1))
        index[f.name] = {"sha256": digest, "read_at": now}
        _write_atomic(index_path, json.dumps(index, ensure_ascii=False, indent=1))
    return summary
