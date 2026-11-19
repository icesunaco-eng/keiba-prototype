"""取込の読み込み部（netkeiba の表・JRA PDF の払戻欄）。合成の小さな断片で試す。"""
from keiba.ingest import jra_pdf, netkeiba

ROW = """<tr class="HorseList"><td class="Result_Num"><div class="Rank">{rank}</div></td>
<td class="Num Waku{w}"><div>{w}</div></td><td class="Num Txt_C"><div>{n}</div></td>
<td class="Horse_Info"><span class="Horse_Name"><a href="https://db.netkeiba.com/horse/20221{n:05d}">
<span class="HorseNameSpan">ウマ{n}</span></a></span></td>
<td class="Horse_Info Txt_C"><div><span>牝3</span></div></td>
<td class="Jockey_Info"><div><span class="JockeyWeight">55.0</span></div></td>
<td class="Jockey"><a href="https://db.netkeiba.com/jockey/result/recent/0110{n}/"><span>▲騎手{n}</span></a></td>
<td class="Time"><span class="RaceTime">1:12.{n}</span></td><td class="Time"><span class="RaceTime">クビ</span></td>
<td class="Odds"><span class="OddsPeople">{n}</span></td><td class="Odds Txt_R"><span>{n}.5</span></td>
<td class="Time BgOrange">35.{n}</td><td class="PassageRate">{n}-{n}</td>
<td class="Trainer"><span>美浦</span><a href="https://db.netkeiba.com/trainer/result/recent/0120{n}/"><span>調教{n}</span></a></td>
<td class="Weight"> 48{n}<small>(+{n})</small></td></tr>"""

PAGE = ("<html><body><h1 class='RaceName'>テスト<span class='Icon_GradeType Icon_GradeType3'></span></h1>"
        "<div class='RaceData01'>10:05発走 / <span> 芝1200m</span> 右 / "
        "<span class='Icon_Weather' title='曇'></span><span class='Item04'> 稍重</span></div>"
        "<div class='RaceData02'><span>1回</span><span>中山</span><span>3歳未勝利</span><span>16頭</span></div>"
        "<table>" + "".join(ROW.format(rank=i, w=i, n=i) for i in (1, 2, 3)) + "</table>"
        "<table class='Payout_Detail_Table'><tr class='Fuku3'><th>3連複</th><td class='Result'><ul>"
        "<li><span>1</span></li><li><span>2</span></li><li><span>3</span></li></ul></td>"
        "<td class='Payout'><span>1,230円</span></td></tr></table></body></html>")


def test_netkeiba_full_page():
    p = netkeiba.parse_page(PAGE)
    assert p["race"]["surface"] == "芝" and p["race"]["distance_m"] == 1200
    assert p["race"]["going"] == "稍重" and p["race"]["weather"] == "曇" and p["race"]["race_class"] == "G3"
    r = p["rows"][0]
    assert r["horse_number"] == 1 and r["sex"] == "牝" and r["age"] == 3 and r["weight_carried"] == 55.0
    assert r["jockey_id"] == "01101" and r["jockey_name"] == "騎手1" and r["trainer_id"] == "01201"
    assert r["body_weight"] == 481 and r["body_weight_diff"] == 1 and r["final_win_odds"] == 1.5
    assert r["horse_id"] == "2022100001" and r["finish_time_s"] == 72.1
    assert p["trio_payouts"] == [{"combo": (1, 2, 3), "payout_yen": 1230}]


def test_netkeiba_header_text_from_capture_metadata():
    h = "1R\n2歳未勝利\n09:50発走 / 芝1800m 右\xa0B /  良\n1回 函館 11日目 サラ系２歳 未勝利 (混)[指] 馬齢 11頭"
    race = netkeiba.parse_header_text(h)
    assert race == {"surface": "芝", "distance_m": 1800, "turn": "右", "going": "良", "field_size": 11,
                    "post_time": "09:50", "race_class": "未勝利"}


def _cid(s):
    return "".join(f"(cid:{9872 + int(ch)})" if ch.isdigit() else ch for ch in s)


def test_jra_pdf_column_payout_and_vote_check():
    text = ("第2競走 3連 複 (cid:3)(cid:4)(cid:2) " + _cid("720") + "円 3連 単 (cid:2)(cid:3)(cid:4) " + _cid("7") + "，"
            + _cid("370") + "円\n3連複票数 計 " + _cid("767390") + " 的中 (cid:3)(cid:4)(cid:2) " + _cid("79296") + "（2番人気）")
    info = jra_pdf.parse_column_text(text)
    assert info["race_numbers"] == [2] and info["trio_payouts"] == [720]
    assert jra_pdf.column_to_payout(info) == {"payout_yen": 720, "check": "votes_ok"}


def test_jra_pdf_amount_does_not_swallow_following_digits():
    info = jra_pdf.parse_column_text("3連複 " + _cid("1") + "，" + _cid("788") + "円" + _cid("1800"))
    assert info["trio_payouts"] == [1788]


def test_expected_payout_rounds_down_to_10_yen():
    assert jra_pdf.expected_trio_payout(767390, 79296) == 720
    assert jra_pdf.expected_trio_payout(100, 1000) == 100  # 元返し
