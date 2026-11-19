"""JRA成績PDF（日・場別）から三連複の払戻を読む。

PDFの数字は独自フォント（cid 9872〜9881 が 0〜9）で、馬番は丸数字の字形（読まない）。
組番は着順から分かるので、ここでは各レースの「3連複 …円」の金額だけを取る。
ページは左右2段で、1段に1レース。段の位置からレースを特定するので、
統合版の jra-baseline/expected_races.jsonl（PDF名・頁・左右 → race_key）を使う。

自己照合：同じ段の「3連複票数 計X 的中Y」から 払戻 ≒ X×0.75÷Y×100（10円未満切捨）を計算し、
読んだ金額と比べる（check = votes_ok / votes_mismatch / no_votes）。
"""
from __future__ import annotations

import re
from pathlib import Path

DIGIT_CID = re.compile(r"\(cid:(\d+)\)")
TAKEOUT_TRIO = 0.25
PARSER_VERSION = "2"  # 読み方を変えたら上げる（キャッシュの作り直し）


def decode(text: str) -> str:
    def repl(m):
        n = int(m.group(1))
        return str(n - 9872) if 9872 <= n <= 9881 else ""
    return DIGIT_CID.sub(repl, text or "")


def _int(s: str) -> int:
    return int(s.replace("，", "").replace(",", ""))


def expected_trio_payout(pool_votes: int, winning_votes: int) -> int:
    value = pool_votes * (1 - TAKEOUT_TRIO) / winning_votes * 100
    return max(100, int(value // 10 * 10))


def parse_column_text(text: str) -> dict:
    t = re.sub(r"\s+", "", decode(text))
    races = re.findall(r"第(\d+)競走", t)
    pays = [_int(p) for p in re.findall(r"3連複(\d{1,3}(?:[，,]\d{3})*)円", t)]
    votes = re.search(r"3連複票数計([\d，,]+)的中([\d，,]+)（", t)
    return {
        "race_numbers": [int(r) for r in races],
        "trio_payouts": pays,
        "pool_votes": _int(votes.group(1)) if votes else None,
        "winning_votes": _int(votes.group(2)) if votes else None,
    }


def extract_pdf(path: Path) -> list[dict]:
    """PDF 1本を読み、段ごとの結果を返す（page は1始まり、side は left/right）。"""
    import pdfplumber

    out = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages):
            w, h = page.width, page.height
            for side, box in (("left", (0, 0, w / 2, h)), ("right", (w / 2, 0, w, h))):
                info = parse_column_text(page.crop(box).extract_text() or "")
                info.update(page=i + 1, side=side, file=path.name)
                out.append(info)
    return out


def column_to_payout(info: dict) -> dict | None:
    """段の読取結果を払戻1件にする。同着などで金額が1つでないときは読まない。"""
    if len(info["trio_payouts"]) != 1:
        return None
    pay = info["trio_payouts"][0]
    if info["pool_votes"] and info["winning_votes"]:
        exp = expected_trio_payout(info["pool_votes"], info["winning_votes"])
        check = "votes_ok" if abs(exp - pay) <= 10 else "votes_mismatch"
    else:
        check = "no_votes"
    return {"payout_yen": pay, "check": check}
