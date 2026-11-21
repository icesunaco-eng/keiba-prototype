"""JRA公式サイト（www.jra.go.jp/JRADB）から、出馬表とオッズのページを取ってくる。

JRAのページは「cname」という値をPOST（またはGETの ?CNAME=）で渡して開く。cname の末尾にはページごとの
確認用の値が付いているので、自分で組み立てず、必ず前のページのリンクからたどる。

取り方の決まり（JRAの「ご利用に際して」と robots.txt を確認済み：自動取得の禁止は無い。著作物は私的使用の範囲）：
  - リクエストの間を MIN_INTERVAL 秒以上あける。1回の実行で取るページ数にも上限を設ける
  - 取ったHTMLは手元にだけ保存する（git にも外部にも出さない）
"""
from __future__ import annotations

import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request

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


    def get_url(self, url: str) -> str:
        """JRAの普通のページ（ニュースの一覧など）を GET で取る。数の上限と間隔は get と同じ。"""
        if not url.startswith("https://www.jra.go.jp/"):
            raise ValueError("JRAのページだけ取る")
        if self.count >= self.max_requests:
            raise RuntimeError("この実行で取るページ数の上限に達した")
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with self._opener(req, timeout=30) as res:
            body = res.read()
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
    for tr in doc.xpath('//td[contains(concat(" ", normalize-space(@class), " "), " horse ")]/..'):
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
        num = _t(tr.xpath('./td[contains(@class,"num")]')[0]) if tr.xpath('./td[contains(@class,"num")]') else ""
        # 取消・除外の印は、実物では馬番の欄（番号の代わり）と馬名の横のオッズ欄に出る。ほかの欄の文字（注記など）は見ない
        marks = " ".join([num, *(_t(x) for x in tr.xpath('./td[contains(@class,"horse")]//div[contains(@class,"odds")]'))])
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
    return rows


# ---------- 結果・オッズ ----------

_JOCKEY = re.compile(r"pw04kmk0(\d{5})")
_TRAINER = re.compile(r"pw05cmk0(\d{5})")


def page_complete(page: str) -> bool:
    """ページが最後（</html>）まで届いているか。途中で切れたページは、払戻の数字が欠けても読めてしまうので使わない。"""
    return bool(re.search(r"</html>\s*$", page or "", re.I))


def parse_result(page: str) -> dict:
    """レース結果ページ。着順の表と三連複の払戻（同着なら複数）。
    ページが最後まで届いていなければ、何も読まずに page_incomplete=True を返す（結果待ちのまま）。"""
    if not page_complete(page):
        return {"rows": [], "trio_payouts": [], "page_incomplete": True}
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


VENUES = ("札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉")
# 例：「本日【2月7日（土曜）】の東京競馬は第8レース以降を取りやめといたします」
#     「本日【2月8日（日曜）】の東京競馬および京都競馬は開催を中止します」
_V = "(?:" + "|".join(VENUES) + ")"
_CANCEL = re.compile(r"^(?:本日)?【(\d{1,2})月(\d{1,2})日[（(][^）)]*[）)]】の"
                     r"(" + _V + r"競馬(?:(?:および|及び|・|、|と)" + _V + r"競馬)*)は"
                     r"(?:第(\d{1,2})レース以降を|第(\d{1,2})レースを|全レースを|開催を)?(?:取りやめ|取り止め|中止)")


def parse_cancellations(page: str, year: int) -> list[dict]:
    """JRAのニュースの月別一覧から、レースの取りやめのお知らせを読む。見出しが決まった形のものだけ。
    返す行：race_date・venue・from_race（この番号から）・to_race（この番号まで。None なら最後まで）・title・href。
    「〜の開催中止に伴う指定席の取扱い」「前日発売を取りやめ」のような関連のお知らせは、形が違うので拾わない。"""
    doc = lxml_html.fromstring(page)
    out = []
    for a in doc.xpath('//li//div[contains(@class,"news_line")]//a'):
        txt = a.xpath('.//div[contains(@class,"txt")]')
        title = _t(txt[0]) if txt else _t(a)
        m = _CANCEL.match(title)
        if not m:
            continue
        mon, day, venues, after, only = m.groups()
        start = int(after or only or 1)
        for venue in re.findall(_V, venues):
            out.append({"race_date": f"{year}-{int(mon):02d}-{int(day):02d}", "venue": venue,
                        "from_race": start, "to_race": int(only) if only else None,
                        "title": title, "href": a.get("href")})
    return out


def _clock(text: str):
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2})\.(\d)", text.strip())
    return int(m.group(1) or 0) * 60 + int(m.group(2)) + int(m.group(3)) / 10 if m else None


def _num(text: str):
    t = text.replace(",", "").strip()
    return float(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


def parse_win_odds(page: str, field: int | None = None) -> dict:
    """単勝オッズ（馬番 → 倍）。表の「馬番」と、単勝の列（class に tan を含むセル、無ければ行の最初の小数）を読む。
    読めた頭数が field と違えば ok=False（使わない）。最初の実物で確かめるまでは、この自己照合だけが頼り。"""
    doc = lxml_html.fromstring(page)
    odds = {}
    for tr in doc.xpath("//table//tr"):
        num = tr.xpath('./*[contains(@class,"num")]')
        if not num or not _t(num[0]).isdigit():
            continue
        tan = tr.xpath('./td[contains(@class,"tan")]') or tr.xpath('./td[contains(@class,"odds")]')
        vals = [_num(_t(x)) for x in tan] if tan else [_num(x) for x in re.findall(r"\d+\.\d", _t(tr))]
        vals = [v for v in vals if v]
        if vals:
            odds.setdefault(int(_t(num[0])), vals[0])
    ok = bool(odds) and (field is None or len(odds) == field)
    return {"odds": odds, "ok": ok}


def parse_trio_odds(page: str, field: int) -> dict:
    """三連複オッズ（"a-b-c" → 倍）。表ごとに見出し（例「1-2」）の2頭と、行の馬番・オッズで組を作る。
    取消の馬がいれば組は減るので、C(出走頭数,3) と一致したときだけ ok=True。"""
    doc = lxml_html.fromstring(page)
    odds, dups, bad = {}, 0, 0
    for table in doc.xpath("//table"):
        cap = table.xpath("./caption") or table.xpath("preceding-sibling::*[1]")
        head = [int(x) for x in re.findall(r"\d+", _t(cap[0]))] if cap else []
        if len(head) != 2:
            continue
        for tr in table.xpath(".//tr"):
            # 実物（2026-11-21 確認）は <th scope="row">3</th><td>1662.9</td>。class が付いた形も読めるようにしておく
            num = tr.xpath('./*[contains(@class,"num")]') or tr.xpath("./th")
            val = tr.xpath('./td[contains(@class,"odds")]') or tr.xpath("./td")
            if not (num and val and _t(num[0]).isdigit()):
                continue
            v = _num(_t(val[0]))
            third = int(_t(num[0]))
            if third in head:
                continue
            if not v:
                bad += 1  # 「取消」「---」など、数字でないオッズ
                continue
            key = "-".join(map(str, sorted(head + [third])))
            if key in odds:
                dups += 1
            odds[key] = v
    expected = math.comb(field, 3) if field and field >= 3 else None
    ok = expected is not None and len(odds) == expected and dups == 0
    return {"odds": odds, "ok": ok, "expected": expected, "found": len(odds), "duplicates": dups, "unreadable": bad}


_ODDS_TIME = re.compile(r"(\d{1,2})時(\d{2})分\s*(?:現在|時点)")


def parse_odds_time(page: str) -> str | None:
    """オッズの発表時刻（例「11時30分現在」）。見つからなければ None（時刻不明）。"""
    m = _ODDS_TIME.search(unicodedata.normalize("NFKC", lxml_html.fromstring(page).text_content()))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None
