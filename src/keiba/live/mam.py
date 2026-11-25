"""文章AI方式（まむ）とのやりとり。設計の3つ目の方式。

出す（export）：レース×時点ごとに、締切までに分かっていた情報だけの「入力の束」（JSON）を data/mam/outbox/ に書く。
  中身：レース条件、出走馬（取消を除く）、各馬の近4走・父・母の父・騎手・調教師、その時点の単勝・三連複オッズ（合格したときだけ）
  入れないもの：学習モデル・ルールの予想（独立させるため）、結果、締切後の情報
受け取る（import）：まむの予想（JSON。形は docs/requests/mam-format.md）を検証して、台帳に method="text_ai" で入れる
  - 作成時刻（generated_at）が、その時点の締切より後なら受け付けない
  - 馬番は、その時点の出走馬（取消を除く）だけ
  - 三連複の確率があれば合計1に直して使う。無ければ順位から組を作るが、確率の裏づけが無いので配分は0円
  - 配分は学習モデルと同じ合格判定・同じ3配分。精算は wallet.run_live がまとめて行う
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import sys
import uuid
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


def _packet_path(paths, rk: str, tp: str) -> Path:
    """まむに渡した入力の置き場所（export_packets が書くファイルと同じ名前）。"""
    return paths.data_dir / "mam" / "outbox" / f"{rk.replace('|', '_')}_{tp}.json"


def _rename_noreplace(src: Path, dst: Path) -> bool:
    """src を dst へ一度に移す。dst がもうあれば移さず False（上書きしない）。
    ハードリンクを作れない置き場のための道。OS がこの操作を持たなければ OSError（置かない）。"""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rc = fn(os.fsencode(src), os.fsencode(dst), 0x4)            # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rc = fn(-100, os.fsencode(src), -100, os.fsencode(dst), 1)  # AT_FDCWD・RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "上書きしない移動が使えない")
    if rc == 0:
        return True
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        return False
    raise OSError(err, os.strerror(err))


def _write_complete(tmp: Path, text: str) -> None:
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)   # 権限は前の write_text と同じ（umask に従う）
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def _publish_once(paths, rk: str, tp: str, text: str) -> Path | None:
    """入力の束を、書き終えた形で一度だけ outbox に置く。同じ名前がもうあれば置かず None を返す。
    書き出しが同時に2つ走っても（見回りと手動の mam export など）、先に置いた入力は入れ替わらない。
    まむが読んだ入力と、受付で確かめる入力（issued_roster）を同じものにするため。
    正式な名前に現れるのは、いつも書き終えた入力だけ（書く途中で止まっても、正式な名前には何も残らず、次の回に置き直せる）。
    1. ふつう：outbox の外（outbox-staging）で書き終えて、ハードリンクで置く
    2. 別のディスクなどでリンクできない：outbox の中の隠し名（.〜.part）で書き終えて、同じ場所でリンクで置く
    3. ハードリンクが使えない置き場：同じ隠し名から「上書きしない移動」で置く。それも使えなければ置かない（OSError）"""
    path = _packet_path(paths, rk, tp)
    stage = paths.data_dir / "mam" / "outbox-staging"   # 同じディスクの outbox の外
    stage.mkdir(parents=True, exist_ok=True)
    tag = f"{path.stem}.{os.getpid()}.{uuid.uuid4().hex}.part"
    tmp = stage / tag
    near = path.parent / f".{tag}"
    try:
        _write_complete(tmp, text)
        try:
            os.link(tmp, path)   # 同じ名前があれば失敗する（上書きしない）。置いた時には中身がそろっている
            return path
        except FileExistsError:
            return None
        except OSError:
            pass
        _write_complete(near, text)
        try:
            os.link(near, path)
            return path
        except FileExistsError:
            return None
        except OSError:
            pass
        return path if _rename_noreplace(near, path) else None
    finally:
        for f in (tmp, near):
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass


def issued_roster(paths, rk: str, tp: str) -> list[int] | None:
    """そのレース・時点でまむに渡した入力（outbox。_publish_once が一度だけ置き、上書きしない）の出走馬。
    無い・読めない・別のレースのものは None（予想と出走馬を結び付けられない）。"""
    try:
        packet = json.loads(_packet_path(paths, rk, tp).read_text(encoding="utf-8"))
        if packet.get("race_key") != rk or packet.get("time_point") != tp:
            return None
        roster = [h["horse_number"] for h in packet["horses"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not roster or any(type(n) is not int for n in roster) or len(set(roster)) != len(roster):
        return None
    return sorted(roster)


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
        placed = _publish_once(paths, rk, tp, json.dumps(packet, ensure_ascii=False, indent=1))
        if placed is None:
            continue   # 同時に動いた別の書き出しが先に置いた。先に置いた入力をそのまま渡す
        written.append(placed)
    return written


PROB_MAX = 1e6  # p は合計1でなくてよいが、足して桁あふれしない範囲（百分率や点数でも足りる）


class Rejected(Exception):
    pass


def _validate(pred: dict, state: dict, inputs: dict) -> tuple[dict, list]:
    if pred.get("schema") != SCHEMA_OUT:
        raise Rejected(f"schema が {SCHEMA_OUT} でない")
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
        p = t.get("p")
        if p is not None:
            # 非数・無限大・負・合計0や桁あふれは、統合の計算が止まって、ほかのレースの予想も道連れになる
            if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= PROB_MAX:
                raise Rejected(f"p は0以上の有限の数（合計1でなくてよい）：{t['combo']} {p!r}")
            p = float(p)
        trios.append(("-".join(map(str, combo)), p))
    given = {c: p for c, p in trios if p is not None}
    if given and not sum(given.values()) > 0:
        raise Rejected("trios の p の合計が0")
    if not ranking and not trios:
        raise Rejected("ranking か trios のどちらかが要る")
    return {"race": r, "nums": nums, "ranking": ranking, "trios": trios, "gen": gen}, sorted(nums)


def import_predictions(paths, file: Path) -> dict:
    root = paths.data_dir / "live"
    state = _state(root)
    _, _, inputs = live_tables(root, state, _snapshots(root))
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
        issued = issued_roster(paths, rk, tp)
        payload = {
            "race_date": v["race"]["race_date"], "method_kind": "text_ai", "uses_post_race_odds": False,
            "horses": [{"n": n, "rank": (v["ranking"].index(n) + 1) if n in v["ranking"] else None} for n in nums],
            "trios": [{"combo": c, "p": round(probs[c], 6) if c in probs else None} for c in ranked[:20]],
            "trios_all": {c: round(p, 7) for c, p in probs.items()},
            # 全組そろいは、予想を作った時にまむが見ていた出走馬（渡した入力）で見る。渡した入力が分からなければ受付の時点の出走馬
            "complete": bool(probs) and set(probs) == {"-".join(map(str, c)) for c in combinations(issued or sorted(nums), 3)},
            # 統合で正式にするかは、これと統合の時の出走馬で決める
            "issued_horses": issued,
            "confidence": pred.get("confidence", "Z"),
            "allocation": {a: (lambda x: {"status": x.status if gate.get("pass") else "not_allocated_gate", "stakes": x.stakes,
                                          "total": x.total})(allocate(probs, odds, a, calibrated=calibrated)) for a in ALLOC_METHODS},
            "odds_gate": gate, "notes": [pred.get("comment", "")][:1], "generated_at": pred["generated_at"],
            "period": "live", "submitted_by": pred.get("author", "mam"),
        }
        added = led.add_prediction(rk, tp, "text_ai", MAM_VERSION, payload, sha(canonical(pred))[:16], "mam", code_hash())
        ok += int(added)
    led.commit()
    led.close()
    return {"accepted": ok, "rejected": rejected}
