"""JRA公式サイト（www.jra.go.jp/JRADB）から、出馬表とオッズのページを取ってくる。

JRAのページは「cname」という値をPOST（またはGETの ?CNAME=）で渡して開く。cname の末尾にはページごとの
確認用の値が付いているので、自分で組み立てず、必ず前のページのリンクからたどる。

取り方の決まり（JRAの「ご利用に際して」と robots.txt を確認済み：自動取得の禁止は無い。著作物は私的使用の範囲）：
  - リクエストの間を MIN_INTERVAL 秒以上あける。1回の実行で取るページ数にも上限を設ける
  - 取ったHTMLは手元にだけ保存する（git にも外部にも出さない）
"""
from __future__ import annotations

import itertools
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from fractions import Fraction

from lxml import html as lxml_html

BASE = "https://www.jra.go.jp"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
MIN_INTERVAL = 2.0
ENTRY_INDEX = ("/JRADB/accessD.html", "pw01dli00/F3")
ODDS_INDEX = ("/JRADB/accessO.html", "pw15oli00/6D")
_ACTION = re.compile(r"doAction\('([^']+)',\s*'([^']+)'\)")
_CNAME = re.compile(r"CNAME=([^\"'&]+)")


class Client:
    def __init__(self, max_requests: int = 300, interval: float = MIN_INTERVAL, opener=None):
        self.max_requests = max_requests
        self.interval = interval
        self.count = 0
        self._last = 0.0
        self._opener = opener or urllib.request.urlopen

    def get(self, path: str, cname: str) -> str:
        if self.count >= self.max_requests:
            raise RuntimeError("この実行で取るページ数の上限に達した")
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        data = urllib.parse.urlencode({"cname": cname}).encode()
        req = urllib.request.Request(BASE + path, data=data, headers={"User-Agent": UA})
        for attempt in range(3):
            try:
                with self._opener(req, timeout=30) as res:
                    body = res.read()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        self._last = time.monotonic()
        self.count += 1
        return body.decode("cp932", errors="replace")


def links(page: str) -> list[tuple[str, str]]:
    """ページ内のリンク（パス, cname）を全部。onclick の doAction と href の ?CNAME= の両方。"""
    out = [(p, c) for p, c in _ACTION.findall(page)]
    for m in re.finditer(r'href="(/JRADB/access\w+\.html)\?CNAME=([^"&]+)"', page):
        out.append((m.group(1), m.group(2)))
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _t(node) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", node.text_content())).strip() if node is not None else ""


def parse_race_list(page: str) -> dict:
    """出馬表の「開催場別レース一覧」。日付・場・各レース（R・発走時刻・距離・頭数・出馬表/オッズのcname）。"""
    doc = lxml_html.fromstring(page)
    title = _t(doc.xpath('//table[@id="race_list"]//h2')[0]) if doc.xpath('//table[@id="race_list"]//h2') else ""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?(\d+)回(\S+?)(\d+)日", title)
    meeting = {}
    if m:
        meeting = {"race_date": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                   "kai": int(m.group(4)), "venue": m.group(5), "nichi": int(m.group(6))}
    races = []
    for tr in doc.xpath('//table[@id="race_list"]/tbody/tr'):
        num = tr.xpath('.//th[contains(@class,"race_num")]//img/@alt')
        rn = re.search(r"(\d+)", num[0]) if num else None
        tm = re.search(r"(\d{1,2})時(\d{2})分", _t(tr.xpath('.//td[contains(@class,"time")]')[0])) if tr.xpath('.//td[contains(@class,"time")]') else None
        row_html = lxml_html.tostring(tr, encoding="unicode")
        card = re.search(r"CNAME=(pw01dde[^\"&]+)", row_html)
        odds_cell = tr.xpath('.//td[contains(@class,"odds")]')
        result_cell = tr.xpath('.//td[contains(@class,"result")]')
        odds_links = links(lxml_html.tostring(odds_cell[0], encoding="unicode")) if odds_cell else []
        dist = tr.xpath('.//td[contains(@class,"dist")]')
        dtext = _t(dist[0]) if dist else ""
        dm = re.search(r"(芝|ダート|障害|ダ)\s*([\d,]+)\s*m?\s*(\d+)?", dtext)
        races.append({
            "race_number": int(rn.group(1)) if rn else None,
            "post_time": f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else None,
            "surface": {"ダ": "ダート"}.get(dm.group(1), dm.group(1)) if dm else None,
            "distance_m": int(dm.group(2).replace(",", "")) if dm else None,
            "field_size": int(dm.group(3)) if dm and dm.group(3) else None,
            "race_name": _t(tr.xpath('.//td[contains(@class,"race_name")]')[0]) if tr.xpath('.//td[contains(@class,"race_name")]') else None,
            "card_cname": card.group(1) if card else None,
            "odds_links": odds_links,
            "odds_status": _t(odds_cell[0]) if odds_cell else None,
            "result_links": links(lxml_html.tostring(result_cell[0], encoding="unicode")) if result_cell else [],
        })
    return {**meeting, "races": [r for r in races if r["race_number"]]}


def parse_card(page: str) -> list[dict]:
    """出馬表（1レース）。馬番・枠・馬ID・性齢・斤量・騎手・調教師・父・母・母の父・近走。"""
    doc = lxml_html.fromstring(page)
    rows = []
    rows = CardRows()
    # 出馬表の表は、実物（2026-11-21〜22 の88ページで確認）ではちょうど1つで、入れ子の表は無い。
    # 「馬番の td.num と馬の td.horse を持つ行」を直接持つ表がちょうど1つ・ページに入れ子の表が無いときだけ読む
    tables = [t for t in doc.xpath("//table") if _entry_rows(t)]
    if _nested(doc):
        rows.problems.append("出馬表のページに入れ子の表がある")
    if _dup_class(page):
        rows.problems.append("出馬表のページに class が2回ある欄がある")
    if len(tables) != 1:
        rows.problems.append(f"出馬表の表が{len(tables)}個")
    for tr in (_entry_rows(tables[0]) if len(tables) == 1 and not rows.problems else []):
        horse = tr.xpath('./td[contains(concat(" ", normalize-space(@class), " "), " horse ")]')[0]
        a = horse.xpath('.//div[@class="name"]/a')
        hid = re.search(r"pw01dud\d{2}(\d{10})", a[0].get("href", "")) if a else None
        age = _t(tr.xpath('.//p[@class="age"]')[0]) if tr.xpath('.//p[@class="age"]') else ""
        am = re.match(r"(牡|牝|セ|騸)(\d+)", age)
        wt = re.search(r"([\d.]+)", _t(tr.xpath('.//td[contains(@class,"jockey")]//p[@class="weight"]')[0])) if tr.xpath('.//td[contains(@class,"jockey")]//p[@class="weight"]') else None
        bm = horse.xpath('.//span[@class="bloodmare"]')
        mare = horse.xpath('.//li[@class="mare"]')
        mare_text = _t(mare[0]).replace("母:", "").replace("母：", "") if mare else ""
        if bm:
            mare_text = mare_text.replace(_t(bm[0]), "").strip()
        past = []
        for td in tr.xpath('./td[contains(@class,"past")]'):
            place = re.search(r"(\d+)", _t(td.xpath('.//div[@class="place"]')[0])) if td.xpath('.//div[@class="place"]') else None
            date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", _t(td.xpath('.//div[@class="date"]')[0])) if td.xpath('.//div[@class="date"]') else None
            if not date:
                continue
            past.append({"date": f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}",
                         "venue": _t(td.xpath('.//div[@class="rc"]')[0]) if td.xpath('.//div[@class="rc"]') else None,
                         "finish": int(place.group(1)) if place else None,
                         "dist": _t(td.xpath('.//span[@class="dist"]')[0]) if td.xpath('.//span[@class="dist"]') else None,
                         "time": _t(td.xpath('.//p[@class="time"]')[0]) if td.xpath('.//p[@class="time"]') else None,
                         "f3": _t(td.xpath('.//div[@class="f3"]')[0]).replace("3F", "").strip() if td.xpath('.//div[@class="f3"]') else None,
                         "corners": [_t(li) for li in td.xpath('.//div[@class="corner_list"]//li')]})
        num_cells = tr.xpath(f"./td[{_has_class('num')}]")
        if len(num_cells) != 1:  # 実物の出走の行は馬番の欄がちょうど1つ。違う行があれば出馬表ごと使わない
            rows.problems.append(f"馬番の欄が{len(num_cells)}個の行がある")
            continue
        num = _t(num_cells[0])
        # 取消・除外は、実物だと馬番の欄（と馬の欄の人気の行）が「取消」だけになる。欄の中身がその語とぴったり同じときだけ取消と見る
        # （馬名の注記などに「取消」の字があっても取消にしない）
        odds_line = horse.xpath('.//div[@class="odds_line"]')
        marks = {num, _t(odds_line[0]) if len(odds_line) == 1 else ""}
        scratch = next((w for w in ("取消", "除外") if w in marks), None)
        waku = tr.xpath('./td[contains(@class,"waku")]//img/@alt')
        rows.append({
            "horse_number": int(num) if num.isdigit() else None,
            "waku": int(re.search(r"\d+", waku[0]).group()) if waku and re.search(r"\d+", waku[0]) else None,
            "horse_id": hid.group(1) if hid else None,
            "horse_name": _t(a[0]) if a else None,
            "sex": {"騸": "セ"}.get(am.group(1), am.group(1)) if am else None,
            "age": int(am.group(2)) if am else None,
            "weight_carried": float(wt.group(1)) if wt else None,
            "jockey_name": _t(tr.xpath('.//td[contains(@class,"jockey")]//p[@class="jockey"]')[0]) if tr.xpath('.//td[contains(@class,"jockey")]//p[@class="jockey"]') else None,
            "jockey_key": (lambda m: f"nk:{m.group(1)}" if m else None)(_JOCKEY.search(lxml_html.tostring(tr.xpath('./td[contains(@class,"jockey")]')[0], encoding="unicode"))) if tr.xpath('./td[contains(@class,"jockey")]') else None,
            "trainer_key": (lambda m: f"nk:{m.group(1)}" if m else None)(_TRAINER.search(lxml_html.tostring(horse, encoding="unicode"))),
            "trainer_name": _t(horse.xpath('.//p[@class="trainer"]/a')[0]) if horse.xpath('.//p[@class="trainer"]/a') else None,
            "sire": _t(horse.xpath('.//li[@class="sire"]')[0]).replace("父:", "").strip() if horse.xpath('.//li[@class="sire"]') else None,
            "dam": mare_text or None,
            "damsire": re.sub(r"[()（）]|母の父[:：]", "", _t(bm[0])).strip() if bm else None,
            "past_runs": past,
            "scratch": scratch,
        })
    if rows.problems:
        del rows[:]  # 問題のある出馬表は、途中まで読めた行も使わない
        return rows
    _infer_scratched_numbers(rows)
    return rows


class CardRows(list):
    """出馬表の行。problems：ページの形の問題（表の数・入れ子）。問題があれば行は空。"""
    def __init__(self, *a):
        super().__init__(*a)
        self.problems = []


def _entry_rows(table):
    return table.xpath(f"./tr[td[{_has_class('num')}] and td[{_has_class('horse')}]]"
                       f" | ./tbody/tr[td[{_has_class('num')}] and td[{_has_class('horse')}]]")


def _nested(doc) -> bool:
    """ページのどこかに、表の中の表があるか（実物の出馬表・オッズのページには無い）。"""
    return bool(doc.xpath("//table//table"))


def _infer_scratched_numbers(rows: list[dict]) -> list[dict]:
    """取消の馬は、実物（2026-11-21 福島11R で確認）だと馬番の欄が「取消」になり、番号が書かれない。
    表は馬番の順なので、番号の無い取消の行が続く区間の前後の番号が、区間の行数ぶんちょうど飛んでいるときだけ
    番号を当てる（horse_number_inferred）。末尾の区間は前の番号から順に当て、頭数の検査（レース一覧）で確かめる。
    当てられないとき（取消でない行の番号が無い・飛びが合わない）は None のまま残し、名簿の検査で不合格にする。"""
    i = 0
    while i < len(rows):
        if rows[i]["horse_number"] is not None or not rows[i].get("scratch"):
            i += 1
            continue
        j = i
        while j < len(rows) and rows[j]["horse_number"] is None and rows[j].get("scratch"):
            j += 1
        prev = rows[i - 1]["horse_number"] if i > 0 else 0
        nxt = rows[j]["horse_number"] if j < len(rows) else None
        k = j - i
        if prev is not None and (nxt == prev + k + 1 or (j == len(rows) and nxt is None)):
            for off, r in enumerate(rows[i:j], start=1):
                r["horse_number"], r["horse_number_inferred"] = prev + off, True
        i = j
    return rows


# ---------- 結果・オッズ ----------

_JOCKEY = re.compile(r"pw04kmk0(\d{5})")
_TRAINER = re.compile(r"pw05cmk0(\d{5})")


def parse_result(page: str) -> dict:
    """レース結果ページ。着順の表と三連複の払戻（同着なら複数）。"""
    doc = lxml_html.fromstring(page)
    rows = []
    for tr in doc.xpath('//table[contains(@class,"striped")]/tbody/tr'):
        cell = lambda c: _t(tr.xpath(f'./td[contains(@class,"{c}")]')[0]) if tr.xpath(f'./td[contains(@class,"{c}")]') else ""
        num = cell("num")
        if not num.isdigit():
            continue
        place = cell("place")
        a = tr.xpath('./td[contains(@class,"horse")]//a')
        hid = re.search(r"pw01dud\d{2}(\d{10})", a[0].get("href", "")) if a else None
        hw = re.match(r"(\d{3})\s*\(([+\-]?\d+)\)", cell("h_weight"))
        corners = [_t(li) for li in tr.xpath('.//div[@class="corner_list"]//li')]
        html_row = lxml_html.tostring(tr, encoding="unicode")
        status = "finished" if place.isdigit() else {"取消": "withdrawn", "除外": "excluded", "中止": "did_not_finish"}.get(
            next((k for k in ("取消", "除外", "中止") if k in place), ""), "did_not_finish" if place else "finished")
        jm, tm_ = _JOCKEY.search(html_row), _TRAINER.search(html_row)
        rows.append({"horse_number": int(num), "finish_position": int(place) if place.isdigit() else None, "status": status,
                     "horse_id": hid.group(1) if hid else None, "last3f_s": float(cell("f_time")) if re.fullmatch(r"\d+\.\d", cell("f_time")) else None,
                     "passage_raw": "-".join(corners) if corners else None,
                     "body_weight": int(hw.group(1)) if hw else None, "body_weight_diff": int(hw.group(2)) if hw else None,
                     "jockey_key": f"nk:{jm.group(1)}" if jm else None, "trainer_key": f"nk:{tm_.group(1)}" if tm_ else None,
                     "final_popularity": int(cell("pop")) if cell("pop").isdigit() else None,
                     "finish_time_s": _clock(cell("time"))})
    trio = []
    for line in doc.xpath('//li[contains(@class,"trio")]//div[@class="line"]'):
        combo = _t(line.xpath('./div[@class="num"]')[0]) if line.xpath('./div[@class="num"]') else ""
        yen = re.sub(r"[^\d]", "", _t(line.xpath('./div[@class="yen"]')[0])) if line.xpath('./div[@class="yen"]') else ""
        nums = [int(x) for x in re.findall(r"\d+", combo)]
        if len(nums) == 3 and yen:
            trio.append({"combo": "-".join(map(str, sorted(nums))), "payout_yen": int(yen)})
    return {"rows": rows, "trio_payouts": trio}


def _clock(text: str):
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2})\.(\d)", text.strip())
    return int(m.group(1) or 0) * 60 + int(m.group(2)) + int(m.group(3)) / 10 if m else None


# 実物の表示は小数1桁（2.4・99,999.9）。それより長い小数・全角数字は読まない。
# 7桁＋小数1桁までなら float にしても表示どおりに戻せるので、上下限と逆数の合計の境目が丸めでずれない
_ODDS_TEXT = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,7})(?:\.[0-9])?")
ODDS_MAX = 999999.9
ODDS_MIN = 1.0  # 払戻は100円につき100円が下限（元返し）。1倍未満は倍率ではない（逆数が無限大になる極小値も入らない）


def _num(text: str):
    """倍率の文字列 → 数。桁区切りは3桁ごとの正しい形だけ（「2,3,4.5」は読まない）。有限の正の数で、1倍以上・上限以下・小数1桁までだけ。"""
    t = text.strip(" \t\r\n\f")
    if not _ODDS_TEXT.fullmatch(t):
        return None
    v = float(t.replace(",", ""))
    return v if math.isfinite(v) and ODDS_MIN <= v <= ODDS_MAX else None


_TAG = re.compile(r"<(?:td|th|table)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.I)


def _dup_class(page: str) -> bool:
    """class が2回書いてある欄があるか。HTMLの読み取り部品は1つ目しか残さないので、生のページで見る（実物には無い）。"""
    for tag in _TAG.findall(page):
        bare = re.sub(r"\"[^\"]*\"|'[^']*'", '""', tag)
        if len(re.findall(r"(?<![\w-])class\s*=", bare, re.I)) > 1:
            return True
    return False


def _raw(node) -> str:
    """倍率の欄の生の文字。_t は全角を半角に直してしまうので、倍率には使わない。"""
    return node.text_content().strip(" \t\r\n\f")


def _has_class(name: str) -> str:
    return f'contains(concat(" ", normalize-space(@class), " "), " {name} ")'


def _rows(table):
    """表そのものの行だけ（入れ子の表の行は含めない）。"""
    return table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")


def parse_win_odds(page: str, field=None) -> dict:
    """単勝オッズ（馬番 → 倍）。実物（2026-11-21 確認）の単勝・複勝の表（class に tanpuku）だけを読む。
    行は「馬番の td.num が1つ・単勝の td.odds_tan が1つ」の形だけ使う。表がちょうど1つでない・馬番が重なるときは不合格。
    field に出走馬（取消を除く）の馬番の並びを渡すと、読めた馬番がそれとぴったり同じときだけ ok=True。"""
    doc = lxml_html.fromstring(page)
    tables = doc.xpath(f"//table[{_has_class('tanpuku')}]")
    odds, dups, bad, problems, seen = {}, 0, 0, [], set()
    if len(tables) != 1:
        problems.append(f"単勝の表が{len(tables)}個")
    if _nested(doc):
        problems.append("入れ子の表がある")
    if _dup_class(page):
        problems.append("class が2回ある欄がある")
    for table in tables[:1]:
        for tr in _rows(table):
            nums, tans = tr.xpath(f"./td[{_has_class('num')}]"), tr.xpath(f"./td[{_has_class('odds_tan')}]")
            if not nums and not tans:
                continue  # 見出しの行など
            # 重複は、行の形の検査より先に、行の中の馬番の欄すべてで数える
            # （形の崩れた「取消」の行でも、どの欄の馬番でもほかの行と同じなら重複）
            for d in (int(_t(x)) for x in nums if _t(x).isdigit()):
                if d in seen:
                    dups += 1
                seen.add(d)
            if len(nums) != 1 or len(tans) != 1 or not _t(nums[0]).isdigit() or tans[0].xpath(".//table"):
                bad += 1
                continue
            n, v = int(_t(nums[0])), _num(_raw(tans[0]))
            if v is None:
                bad += 1  # 「取消」など
                continue
            odds[n] = v
    if dups:
        problems.append(f"馬番の重複{dups}")
    if field is None:
        ok = bool(odds) and not problems
    elif isinstance(field, int):
        ok = bool(odds) and not problems and len(odds) == field
    else:
        roster = [int(x) for x in field]
        ok = bool(odds) and not problems and len(roster) == len(set(roster)) and set(odds) == set(roster)
    return {"odds": odds, "ok": ok, "unreadable": bad, "problems": problems}


def _expected_set(horses) -> tuple[set[str] | None, int | None]:
    """horses：出走馬（取消を除く）の馬番の並び。頭数（int）も受けるが、そのときは組の数でしか照らせない。"""
    if horses is None:
        return None, None
    if isinstance(horses, int):
        return None, (math.comb(horses, 3) if horses >= 3 else None)
    raw = [int(h) for h in horses]
    if len(raw) != len(set(raw)):
        return set(), None  # 名簿に同じ馬番が2回ある：照らす相手が信用できないので不合格にする
    combos = {"-".join(map(str, c)) for c in itertools.combinations(sorted(raw), 3)}
    return combos, (len(combos) or None)


def parse_trio_odds(page: str, horses) -> dict:
    """三連複オッズ（"a-b-c" → 倍）。実物（2026-11-21 確認）の三連複の表（class に fuku3）だけを読む。
    表の見出し（caption）がちょうど「a-b」の形、行は「馬番の th が1つ・倍率の td が1つ」の形だけ使う。
    読めた組が「出走馬（取消を除く）の全組」とぴったり同じとき（多くも少なくもなく、重複なし）だけ ok=True。
    票数なし（実物 2026-11-22 東京3R：<td class="zero">票数なし</td>）は「組はあるが倍率なし」：
    組としては数え、odds には入れない（買わない）。欄の class と文字の両方がぴったりのときだけ。"""
    doc = lxml_html.fromstring(page)
    odds, dups, bad, seen, no_votes = {}, 0, 0, set(), set()
    nested = _nested(doc)  # 実物のオッズのページには入れ子の表が無い。あれば全体を不合格にする
    for table in ([] if nested else doc.xpath(f"//table[{_has_class('fuku3')}]")):
        cap = table.xpath("./caption")
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", _t(cap[0])) if len(cap) == 1 else None
        if not m or m.group(1) == m.group(2):
            bad += 1
            continue
        head = [int(m.group(1)), int(m.group(2))]
        for tr in _rows(table):
            ths, tds = tr.xpath("./th"), tr.xpath("./td")
            if not ths and not tds:
                continue
            # 重複は、行の形の検査より先に、行の中の馬番の欄すべてで数える（形の崩れた「取消」の行でも、どの欄でも同じ組なら重複）
            for d in (int(_t(x)) for x in ths if _t(x).isdigit()):
                if d in head:
                    continue
                key = "-".join(map(str, sorted(head + [d])))
                if key in seen:
                    dups += 1
                seen.add(key)
            if len(ths) != 1 or len(tds) != 1 or not _t(ths[0]).isdigit() or tds[0].xpath(".//table"):
                bad += 1  # 形が違う行は使わない
                continue
            third, v = int(_t(ths[0])), _num(_raw(tds[0]))
            if third in head:
                bad += 1
                continue
            key = "-".join(map(str, sorted(head + [third])))
            if _is_no_votes(tds[0]):
                no_votes.add(key)
                continue
            if "zero" in (tds[0].get("class") or "").split():
                bad += 1  # 票数なしの印なのに形か文字が違う（倍率が書いてあっても使わない）
                continue
            if v is None:
                bad += 1  # 「取消」「---」など
                continue
            odds[key] = v
    want, expected = _expected_set(horses)
    combos = set(odds) | no_votes
    # 票数なしを除いた倍率の逆数の合計は、払戻率が100%以下なら1以上になる（実物の97ページでは最小1.334）。
    # 1未満なら、票のある組まで「票数なし」に読めている（倍率が欠けている）ので使わない
    # 小数の誤差で境目（ちょうど1）を落とさないよう、表示どおりの10進数を分数で足す
    implausible = bool(no_votes) and sum(1 / Fraction(str(v)) for v in odds.values()) < 1
    sound = dups == 0 and not nested and not _dup_class(page)
    if want is not None:
        extra, missing = sorted(combos - want), sorted(want - combos)
        complete = bool(want) and not extra and not missing and sound
    else:
        extra, missing = [], []
        complete = expected is not None and len(combos) == expected and sound
    ok = complete and not implausible
    return {"odds": odds, "ok": ok, "expected": expected, "found": len(combos), "duplicates": dups, "unreadable": bad,
            "no_votes": sorted(no_votes), "implausible": implausible, "complete": complete,
            "nested_table": nested,
            "extra": len(extra), "missing": len(missing), "extra_examples": extra[:5], "missing_examples": missing[:5]}


def _is_no_votes(td) -> bool:
    """実物の形とぴったり同じ欄だけ：class は zero だけ、中は文字だけで、前後はHTMLの空白（半角・タブ・改行）だけ。
    str.split / str.strip は全角や &nbsp; も空白とみなすので使わない。"""
    return td.get("class") == "zero" and len(td) == 0 and (td.text or "").strip(" \t\r\n\f") == "票数なし"


_ODDS_TIME = re.compile(r"(\d{1,2})時(\d{2})分現在オッズ")


def parse_odds_time(page: str) -> str | None:
    """オッズの発表時刻。実物（2026-11-21〜22 確認）は <div class="cell time"><strong>8時50分現在オッズ</strong></div>。
    class に cell と time を持つ欄のうち、中身（空白を除く）がちょうど「○時○○分現在オッズ」のものが1つだけで、
    ありえる時刻のときだけ返す。ほかは None（時刻不明）。"""
    doc = lxml_html.fromstring(page)
    found = []
    for div in doc.xpath(f"//div[{_has_class('cell')} and {_has_class('time')}]"):
        m = _ODDS_TIME.fullmatch(re.sub(r"\s+", "", unicodedata.normalize("NFKC", div.text_content())))
        if m:
            found.append((int(m.group(1)), int(m.group(2))))
    if len(found) != 1:
        return None
    h, mi = found[0]
    return f"{h:02d}:{mi:02d}" if 0 <= h <= 23 and 0 <= mi <= 59 else None
