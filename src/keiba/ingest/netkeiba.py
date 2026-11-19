"""netkeiba の結果ページ（全体のHTML、または結果表だけのDOM）を読む。

使うのは、出走表に相当する列（枠・性齢・斤量・騎手・調教師・馬体重など）、レースの条件（芝ダ・距離・馬場など）、
三連複の払戻。着順・タイムは統合版を正とし、ここの値は食い違いの確認にだけ使う。
"""
from __future__ import annotations

import re
import unicodedata

from lxml import html as lxml_html

_HORSE_ID = re.compile(r"/horse/(\d{10})")
_JOCKEY_ID = re.compile(r"/jockey/(?:result/recent/)?(\w+)/?")
_TRAINER_ID = re.compile(r"/trainer/(?:result/recent/)?(\w+)/?")
_COURSE = re.compile(r"(芝|ダ|障)\s*(?:芝|ダ)?\s*(\d{3,4})m")
_GOING = re.compile(r"(?:馬場[:：]\s*)?(良|稍重|稍|重|不良|不)\b")
_FIELD = re.compile(r"(\d{1,2})頭")
_POST = re.compile(r"(\d{1,2}):(\d{2})発走")
_TURN = re.compile(r"(右|左|直線)")

SURFACE = {"芝": "芝", "ダ": "ダート", "障": "障害"}
GOING_NORMAL = {"良": "良", "稍": "稍重", "稍重": "稍重", "重": "重", "不": "不良", "不良": "不良"}
CLASS_WORDS = [
    ("新馬", "新馬"), ("未勝利", "未勝利"), ("1勝クラス", "1勝"), ("500万下", "1勝"),
    ("2勝クラス", "2勝"), ("1000万下", "2勝"), ("3勝クラス", "3勝"), ("1600万下", "3勝"),
    ("G1", "G1"), ("G2", "G2"), ("G3", "G3"), ("リステッド", "L"), ("オープン", "OP"),
]
GRADE_ICON = {"Icon_GradeType1": "G1", "Icon_GradeType2": "G2", "Icon_GradeType3": "G3",
              "Icon_GradeType15": "L", "Icon_GradeType5": "OP"}


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text_content()).strip() if node is not None else ""


def _num(value: str):
    value = unicodedata.normalize("NFKC", value or "").strip().replace(",", "")
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return None


def parse_time(value: str):
    """'1:49.0' や '58.3' を秒にする。読めなければ None。"""
    value = unicodedata.normalize("NFKC", value or "").strip()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2})\.(\d)", value)
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2)) + int(m.group(3)) / 10


def parse_header_text(text: str) -> dict:
    """レース条件の文字列（RaceData01/02 や capture_metadata の header_text）を読む。"""
    t = unicodedata.normalize("NFKC", text or "").replace("\xa0", " ")
    out: dict = {}
    m = _COURSE.search(t)
    if m:
        out["surface"] = SURFACE[m.group(1)]
        out["distance_m"] = int(m.group(2))
        rest = t[m.end(): m.end() + 12]
        tm = _TURN.search(rest)
        if tm:
            out["turn"] = tm.group(1)
    gm = re.search(r"/\s*(?:天候[:：]\s*\S+\s*/\s*)?(?:芝[:：]|ダート[:：]|馬場[:：])?\s*(良|稍重|稍|重|不良|不)\s*(?:\n|$|/)", t)
    if gm:
        out["going"] = GOING_NORMAL[gm.group(1)]
    fm = _FIELD.search(t)
    if fm:
        out["field_size"] = int(fm.group(1))
    pm = _POST.search(t)
    if pm:
        out["post_time"] = f"{int(pm.group(1)):02d}:{pm.group(2)}"
    for word, cls in CLASS_WORDS:
        if word in t:
            out["race_class"] = cls
            break
    return out


def parse_rows(doc) -> list[dict]:
    rows = []
    for tr in doc.xpath('//tr[contains(concat(" ", normalize-space(@class), " "), " HorseList ")]'):
        tds = tr.xpath("./td")
        if len(tds) < 13:
            continue
        rank = _text(tds[0])
        horse_a = tr.xpath('.//td[contains(@class,"Horse_Info")]//a[contains(@href,"/horse/")]')
        jockey_a = tr.xpath('.//td[contains(@class,"Jockey")]//a[contains(@href,"/jockey/")]')
        trainer_a = tr.xpath('.//td[contains(@class,"Trainer")]//a[contains(@href,"/trainer/")]')
        sexage = unicodedata.normalize("NFKC", _text(tds[4]))
        weight_cell = _text(tr.xpath('.//td[contains(@class,"Weight")]')[0]) if tr.xpath('.//td[contains(@class,"Weight")]') else ""
        wm = re.match(r"(\d{3})\s*\(([+\-]?\d+|前計不|計不)?\)", unicodedata.normalize("NFKC", weight_cell))
        times = [_text(x) for x in tr.xpath('.//td[contains(@class,"Time")]')]
        odds_cells = tr.xpath('.//td[contains(@class,"Odds")]')
        row = {
            "rank_raw": rank,
            "waku": _num(_text(tds[1])),
            "horse_number": _num(_text(tds[2])),
            "horse_name": _text(tr.xpath('.//span[contains(@class,"HorseName")]')[0]) if tr.xpath('.//span[contains(@class,"HorseName")]') else _text(tds[3]),
            "horse_id": (_HORSE_ID.search(horse_a[0].get("href")).group(1) if horse_a and _HORSE_ID.search(horse_a[0].get("href")) else None),
            "sex": sexage[:1] if sexage else None,
            "age": _num(sexage[1:]) if sexage else None,
            "weight_carried": _num(_text(tds[5])),
            "jockey_name": unicodedata.normalize("NFKC", _text(tds[6])).lstrip("▲△☆★◇ "),
            "jockey_id": (_JOCKEY_ID.search(jockey_a[0].get("href")).group(1) if jockey_a and _JOCKEY_ID.search(jockey_a[0].get("href")) else None),
            "finish_time_s": parse_time(times[0]) if times else None,
            "margin_raw": times[1] if len(times) > 1 else None,
            "final_popularity": _num(_text(odds_cells[0])) if odds_cells else None,
            "final_win_odds": _num(_text(odds_cells[1])) if len(odds_cells) > 1 else None,
            "last3f_s": _num(times[2]) if len(times) > 2 else None,
            "passage_raw": _text(tr.xpath('.//td[contains(@class,"PassageRate")]')[0]) if tr.xpath('.//td[contains(@class,"PassageRate")]') else None,
            "trainer_name": _text(trainer_a[0]) if trainer_a else None,
            "trainer_id": (_TRAINER_ID.search(trainer_a[0].get("href")).group(1) if trainer_a and _TRAINER_ID.search(trainer_a[0].get("href")) else None),
            "body_weight": int(wm.group(1)) if wm else None,
            "body_weight_diff": _num(wm.group(2)) if wm and wm.group(2) and wm.group(2)[-1].isdigit() else None,
        }
        if row["horse_number"] is None or not row["rank_raw"] or any(r["horse_number"] == row["horse_number"] for r in rows):
            continue  # 全体ページには別の表（ラップ・注目馬など）にも同じclassの行がある
        rows.append(row)
    return rows


def parse_trio_payouts(doc) -> list[dict]:
    """三連複の払戻。同着で複数あるときは全部返す。"""
    out = []
    for tr in doc.xpath('//table[contains(@class,"Payout_Detail_Table")]//tr[contains(@class,"Fuku3")]'):
        nums = [int(x) for x in re.findall(r"\d+", " ".join(s.text_content() for s in tr.xpath('.//td[contains(@class,"Result")]//span')))]
        pay_text = unicodedata.normalize("NFKC", tr.xpath('.//td[contains(@class,"Payout")]')[0].text_content())
        pays = [int(p.replace(",", "")) for p in re.findall(r"([\d,]+)円", pay_text)]
        combos = [tuple(sorted(nums[i:i + 3])) for i in range(0, len(nums) - len(nums) % 3, 3)]
        if combos and len(combos) == len(pays):
            out.extend({"combo": c, "payout_yen": p} for c, p in zip(combos, pays))
    return out


def parse_page(html_text: str, header_text: str | None = None) -> dict:
    """1レース分のHTMLを読む。header_text は表だけのDOMのとき capture_metadata から渡す。"""
    doc = lxml_html.fromstring(html_text)
    if header_text is None:
        parts = [_text(n) for n in doc.xpath('//div[contains(@class,"RaceData01")] | //div[contains(@class,"RaceData02")]')]
        weather = doc.xpath('//div[contains(@class,"RaceData01")]//span[contains(@class,"Icon_Weather")]/@title')
        header_text = " / ".join(parts)
        race = parse_header_text(header_text.replace(" / ", "\n", 1))
        going = doc.xpath('//div[contains(@class,"RaceData01")]//span[contains(@class,"Item04")]')
        if going:
            g = unicodedata.normalize("NFKC", _text(going[0])).replace("馬場:", "").strip()
            if g in GOING_NORMAL:
                race["going"] = GOING_NORMAL[g]
        if weather:
            race["weather"] = weather[0]
        icons = " ".join(doc.xpath('//h1[contains(@class,"RaceName")]//span/@class'))
        for icon, grade in GRADE_ICON.items():
            if re.search(icon + r"\b", icons):
                race["race_class"] = grade
                break
    else:
        race = parse_header_text(header_text)
    return {"race": race, "rows": parse_rows(doc), "trio_payouts": parse_trio_payouts(doc)}
