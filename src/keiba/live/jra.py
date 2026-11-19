"""JRA公式サイト（www.jra.go.jp/JRADB）から、出馬表とオッズのページを取ってくる。

JRAのページは「cname」という値をPOST（またはGETの ?CNAME=）で渡して開く。cname の末尾にはページごとの
確認用の値が付いているので、自分で組み立てず、必ず前のページのリンクからたどる。

取り方の決まり（JRAの「ご利用に際して」と robots.txt を確認済み：自動取得の禁止は無い。著作物は私的使用の範囲）：
  - リクエストの間を MIN_INTERVAL 秒以上あける。1回の実行で取るページ数にも上限を設ける
  - 取ったHTMLは手元にだけ保存する（git にも外部にも出さない）
"""
from __future__ import annotations

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
            "trainer_name": _t(horse.xpath('.//p[@class="trainer"]/a')[0]) if horse.xpath('.//p[@class="trainer"]/a') else None,
            "sire": _t(horse.xpath('.//li[@class="sire"]')[0]).replace("父:", "").strip() if horse.xpath('.//li[@class="sire"]') else None,
            "dam": mare_text or None,
            "damsire": re.sub(r"[()（）]|母の父[:：]", "", _t(bm[0])).strip() if bm else None,
            "past_runs": past,
        })
    return rows
