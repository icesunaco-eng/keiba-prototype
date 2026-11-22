"""台帳に保存済みの予想・精算を、使う前に検査する（みに💚20261122-3）。

台帳は追記専用なので、検査のゆるかった版が受け付けた値（非数・負の確率など）も残り続ける。
読む側（統合・精算・仮想財布・月次）は、ここを通らない行を使わない。台帳の行は書き換えない。
どの関数も例外を出さず、使えない理由（使えれば None）を返す。
"""
from __future__ import annotations

from ..allocate import RACE_CAP, UNIT

EPS = 1e-6  # 保存時の丸め（7桁）の分


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _combo(key, roster: set | None) -> bool:
    if not isinstance(key, str):
        return False
    parts = key.split("-")
    if len(parts) != 3 or not all(x.isascii() and x.isdigit() and len(x) < 4 for x in parts):
        return False
    a, b, c = map(int, parts)
    return 0 < a < b < c and key == f"{a}-{b}-{c}" and (roster is None or {a, b, c} <= roster)


def probs_problem(probs, roster: set | None = None) -> str | None:
    """{組: 確率}。空は「確率なし」なので問題にしない（配分しない側で扱う）。"""
    if not isinstance(probs, dict):
        return "確率が組と数の対応でない"
    if not probs:
        return None
    for key, v in probs.items():
        if not _combo(key, roster):
            return f"確率の組が出走馬と合わない：{key!r}"[:80]
        if not _num(v) or not 0 <= v <= 1 + EPS:  # 非数はどの比較も偽なので、ここで落ちる
            return f"確率が0〜1の数でない：{key}"
    if not sum(probs.values()) > 0:
        return "確率の合計が0"
    return None


def prediction_problem(p, for_money: bool = False) -> str | None:
    """for_money：精算・仮想財布用。表示用の項目は無くてもよい（入っていれば検査する）。金額と確率はどちらでも検査する。"""
    try:
        if for_money:
            p = {"horses": [], "trios": [], "race_date": "", **p}
        horses = p.get("horses")
        if not isinstance(horses, list) or not all(isinstance(h, dict) and type(h.get("n")) is int for h in horses):
            return "馬の一覧が読めない"
        roster = {h["n"] for h in horses} or None  # 一覧が空なら名簿とは照らさない（金額・確率は見る。統合では今の出走馬と照らす）
        if not isinstance(p.get("race_date"), str):
            return "開催日が読めない"
        if not all(h.get("coverage") is None or (_num(h["coverage"]) and 0 <= h["coverage"] <= 1 + EPS) for h in horses):
            return "採点できた割合が読めない"
        if p.get("rule_view") is not None and not isinstance(p["rule_view"], dict):
            return "ルールの見立てが読めない"
        trios = p.get("trios")
        if not isinstance(trios, list):
            return "組の一覧が読めない"
        for t in trios:
            if not isinstance(t, dict) or not _combo(t.get("combo"), roster):
                return "組の一覧が出走馬と合わない"
            v = t.get("p")
            if v is not None and (not _num(v) or not 0 <= v <= 1 + EPS):
                return f"組の確率が0〜1の数でない：{t['combo']}"
        for key in ("trios_all", "trio_probs_full"):
            if p.get(key) is not None:
                why = probs_problem(p[key], roster)
                if why:
                    return why
        alloc = p.get("allocation")
        if alloc is not None and not isinstance(alloc, dict):
            return "配分が読めない"
        for a in (alloc or {}).values():
            if not isinstance(a, dict):
                continue  # status などの文字
            stakes = a.get("stakes", {})
            if not isinstance(stakes, dict) or not all(
                    _combo(k, roster) and type(v) is int and 0 < v <= RACE_CAP and v % UNIT == 0 for k, v in stakes.items()):
                return "配分の金額が読めない"
            if sum(stakes.values()) > RACE_CAP:
                return "配分が1レースの上限を超える"
    except Exception as exc:
        return f"保存済みの予想が読めない：{type(exc).__name__}"
    return None


def settlement_problem(s) -> str | None:
    try:
        if not (type(s["stake"]) is int and 0 <= s["stake"] <= RACE_CAP and type(s["returned"]) is int and s["returned"] >= 0):
            return "精算の金額が読めない"
        if not isinstance(s.get("hits", []), list):
            return "的中の一覧が読めない"
    except Exception as exc:
        return f"保存済みの精算が読めない：{type(exc).__name__}"
    return None
