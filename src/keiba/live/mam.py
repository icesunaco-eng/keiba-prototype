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


def _publish_once(paths, rk: str, tp: str, text: str, cutoff: datetime | None = None, clock=None) -> Path | None:
    """入力の束を、書き終えた形で一度だけ outbox に置く。同じ名前がもうあれば置かず None を返す。
    cutoff と clock（今の時刻を返す関数）を渡すと、正式な名前に置く直前に毎回時計を見直し、締切を過ぎていれば置かず None を返す
    （見回りの書き出しの途中で締切をまたいでも、締切後の入力を置かない。見直しと置く操作のあいだは、リンク1回分だけ残る）。
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
    def late() -> bool:   # 置く直前の締切の見直し（見回りだけ。手の mam export は締切を問わない）
        return cutoff is not None and clock is not None and clock() >= cutoff

    try:
        _write_complete(tmp, text)
        if late():
            return None
        try:
            os.link(tmp, path)   # 同じ名前があれば失敗する（上書きしない）。置いた時には中身がそろっている
            return path
        except FileExistsError:
            return None
        except OSError:
            pass
        _write_complete(near, text)
        if late():
            return None
        try:
            os.link(near, path)
            return path
        except FileExistsError:
            return None
        except OSError:
            pass
        if late():
            return None
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


def _fetched_before(fetched, dl: datetime) -> bool:
    try:
        return datetime.fromisoformat(fetched) <= dl
    except (TypeError, ValueError):   # 読めない時刻・タイムゾーンの無い時刻は、締切前と数えない
        return False


def _material(s, r: dict, tp: str):
    """そのレースの保存したページの記録 s（_snapshots の行）のうち、時点 tp の入力を作るのに読む行の番号（live_tables と同じ選び方）。
    読むのは、締切までに保存した最後の出馬表と、その時点（time_point が tp）の記録だけ。ほかの時点のページは読まない
    （締切を過ぎた24時間前のオッズのページが読めなくても、1時間前の入力は置ける）。
    入力を作れない（締切までの出馬表が無い・15分前で新しいオッズが無い）なら None。
    置けていない時点を数える unplaced と、その時点だけ読んで置く export_packets が、この同じ条件を使う。
    発走時刻・締切が分からなければ KeyError・TypeError・ValueError を上げる。"""
    dl = deadline(r["race_date"], r["post_time"], tp)
    if not len(s):
        return None
    before = s["fetched_at"].map(lambda x: _fetched_before(x, dl))
    cards = s[(s["kind"] == "card") & before]
    if not len(cards):
        return None
    at_tp = s[s["time_point"] == tp]
    if tp == "15m":   # 15分前は新しいオッズ（締切までの単勝・馬連のページ、または三連複のページ）が無ければ作らない
        win = at_tp["kind"].isin(["odds_win", "odds_first"]) & before[at_tp.index]
        if not (win | (at_tp["kind"] == "odds_trio")).any():
            return None
    return [cards.index[-1], *at_tp.index]


def unplaced(paths, now: datetime) -> set[tuple[str, str]]:
    """締切前なのに、まむへの入力がまだ置かれていない時点（レース×時点）。
    数えるのは、export_packets が入力を作る時点と同じ条件（_material）：その時点の締切までに出馬表のページを保存してあり、
    15分前はその時点のオッズのページもある。取得済みの記録（state の taken）を書く前に止まっても、ページが保存されていれば数える。
    発走時刻・締切の分からない時点は数えない。置き場を調べられないとき（OSError）はそのまま上げる。"""
    root = paths.data_dir / "live"
    state = _state(root)
    snaps = None
    due = set()
    for rk, r in state.get("races", {}).items():
        if not r or not r.get("post_time"):
            continue
        open_tps = []
        for tp in ("24h", "1h", "15m"):
            try:
                dl = deadline(r["race_date"], r["post_time"], tp)
            except (KeyError, TypeError, ValueError):
                continue
            if now < dl and not _packet_path(paths, rk, tp).exists():
                open_tps.append(tp)
        if not open_tps:
            continue   # 締切を過ぎた・もう置いたレースは、ページの記録を見ない
        if snaps is None:
            snaps = _snapshots(root)
        s = snaps[snaps["race_key"] == rk] if len(snaps) else snaps
        due |= {(rk, tp) for tp in open_tps if _material(s, r, tp) is not None}
    return due


def export_packets(paths, now=None, only: set | None = None, held: dict | None = None) -> list[Path]:
    """まだ置いていない入力を置く。
    now を渡すと、締切を過ぎた時点は置かない（見回りはいつも渡す）。now に今の時刻を返す関数（Collector.now）を渡すと、
    選ぶときに加えて、正式な名前に置く直前にも時計を見直す（書き出しの途中で締切をまたいだ時点は置かない）。
    only を渡すと、その時点（レース×時点）だけを、その時点の入力に使うページ（_material）だけ読んで置く。
    1つの時点のページが読めなくても、ほかの時点は置く。held に辞書を渡すと、only のうち入力を作れなかった時点と理由を入れる。
    手で動かす mam export は now を渡さない（締切を問わず置いて復旧できる）。"""
    clock = now if callable(now) else (lambda: now) if now is not None else None
    root = paths.data_dir / "live"
    state = _state(root)
    snaps = _snapshots(root)
    if only is None:
        _, _, inputs = live_tables(root, state, snaps)
    else:
        inputs = {}
        for rk, tp in sorted(only):
            r = state.get("races", {}).get(rk)
            if not r:
                continue
            s = snaps[snaps["race_key"] == rk] if len(snaps) else snaps
            try:
                rows = _material(s, r, tp)
            except (KeyError, TypeError, ValueError):
                continue
            if rows is None:
                continue
            why = {}
            _, _, got = live_tables(root, dict(state, races={rk: r}), snaps.loc[sorted(set(rows))], held=why)
            if (rk, tp) in got:
                inputs[(rk, tp)] = got[(rk, tp)]
            elif held is not None:   # ページが読めない・出馬表が使えない・枠順の確定前など。見回りの結果に出す
                held[f"{rk}|{tp}"] = why.get(f"{rk}|{tp}") or why.get(rk) or "入力を作れない（枠順の確定前など）"
    out_dir = paths.data_dir / "mam" / "outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for (rk, tp), inp in sorted(inputs.items()):
        if tp == "result" or (only is not None and (rk, tp) not in only):
            continue
        r = state["races"][rk]
        dl = deadline(r["race_date"], r["post_time"], tp)
        if clock is not None and clock() >= dl:
            continue   # 締切を過ぎた時点は置かない（まむが答えても受け付けない）
        f = _packet_path(paths, rk, tp)   # 置く場所・受付で読む場所と同じ名前
        if f.exists():
            continue
        gate = inp["gate"]
        packet = {
            "schema": SCHEMA_IN, "race_key": rk, "time_point": tp,
            "deadline": dl.isoformat(timespec="minutes"),
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
        placed = _publish_once(paths, rk, tp, json.dumps(packet, ensure_ascii=False, indent=1), cutoff=dl, clock=clock)
        if placed is None:
            continue   # 同時に動いた別の書き出しが先に置いた・置く直前に締切を過ぎた
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
