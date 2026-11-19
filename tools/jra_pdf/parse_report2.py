"""v2（🤍20261117-3）：JRA レース成績PDF 1開催日分を、pdf_text2.py の位置つき断片から競走・出走馬に分ける。
v1 からの変更（根本原因の修正）：
- 位置は pdf_text2.py（字形の幅と TJ の間隔で x を進める）を使う。v1 は同じ行の連続した文字列を行頭の x に置いており列がずれた
- 列の境界は v2 の座標で原本の列位置に合わせて定めた（下の COLS）。値の型・値域で全行を検査し、外れた値は採用せず理由を残す
- 斤量は「負担重量」（x 130〜139 の同じ行）だけを読む。減量騎手の減量前の重量（x 139〜147、上下にずれた行）は連結しない
- タイムは m:ss.s と ss.s（1分未満）の両方を読む
- 行順（row_order）と確定着順（finish_pos）を分ける。着差欄の「同着」は直前の行と同じ着順。次の馬の着順は前にいる完走馬の数＋1
- 状態（row_state）を全行に付ける：確定着順／同着／競走中止／競走除外／出走取消／失格／降着／判読不能（理由つき）
推測で値を補わない。
使い方: parse_report2.py <pieces.json> <meta.json> <out.json>
"""
import json, re, sys, unicodedata
from collections import Counter, defaultdict

N = lambda s: unicodedata.normalize("NFKC", s)
HALF, RIGHT_X0 = 420.0, 411.0
COLS = [("waku", 18, 25.5), ("umaban", 25.5, 36), ("name", 45, 108), ("sexage", 108, 123), ("color", 123, 130),
        ("carried", 130, 139), ("base_weight", 139, 147), ("mark", 147, 156), ("jockey", 156, 191), ("owner", 191, 236),
        ("trainer", 236, 264), ("breeder", 264, 304), ("gear", 304, 308), ("body", 308, 320.5), ("body_diff", 320.5, 333),
        ("time", 333, 356), ("margin", 356, 378), ("odds", 378, 402), ("pop", 402, 420)]
STATUS_WORDS = ("競走除外", "出走取消", "競走中止", "失格", "降着")


def col_of(xn):
    for c, lo, hi in COLS:
        if lo <= xn < hi:
            return c
    return None


TIME_M = re.compile(r"([1-7]):([0-5]\d)\.(\d)")
TIME_S = re.compile(r"([2-5]\d)\.(\d)")


def split_body_time(comb):
    """馬体重（3桁）・増減・タイムをつないだ文字列を、あり得る分け方の中で「使わない文字が最も少ない」ものに分ける。
    増減は ±0／―／符号＋2桁／符号＋1桁／なし を順に試す。タイムは m:ss.s（分1〜7・秒00〜59）か ss.s（20〜59秒）。
    戻り値：(馬体重, 増減, タイム, 形式, 使わなかった文字) または None（3桁の馬体重もタイムも取れない）。"""
    m = re.match(r"(\d{3})?", comb)
    bw = m.group(1); rest0 = comb[m.end():]
    best = None
    opts = [None]
    for pat in (r"±0", r"―", r"[+\-−]\d{2}", r"[+\-−]\d"):
        mm = re.match(pat, rest0)
        if mm:
            opts.append(mm.group(0))
    for d in opts:
        rest = rest0[len(d):] if d else rest0
        if rest and not re.fullmatch(r"[\d:.]*", rest):
            continue
        for rx, kind in ((TIME_M, "m:ss.s"), (TIME_S, "ss.s")):
            t = rx.search(rest)
            if t:
                left = rest[:t.start()] + rest[t.end():]
                cand = (len(left), 0 if kind == "m:ss.s" else 1, bw, d, t.group(0), kind, left)
                break
        else:
            cand = (len(rest) + 100, 2, bw, d, None, None, rest)
        if best is None or cand[:2] < best[:2]:
            best = cand
    if best is None or (not best[2] and not best[4]):
        return None
    return best[2], best[3], best[4], best[5], best[6]


WAKU_X = 21.0   # 左半分の枠番の x（列の境界はこの位置を基準に決めた）


def name_on_row(qs, y):
    """馬名は行の高さの片仮名だけ（修正 🤍20261117-3）。改名馬は旧馬名「(クリノロンサール)」が約4pt下に書かれ、
    x で並べると現馬名に混ざっていた（例「グランドクロリノロンサールス」）。行に最も近い高さの断片だけを使う。旧馬名は horse_name_raw に残る"""
    ks = [q for q in qs if re.fullmatch(r"[ァ-ヶー・ヴ]+", q["tn"])]
    if not ks:
        return None
    yl = min(ks, key=lambda q: abs(q["y"] - y))["y"]
    return "".join(q["tn"] for q in sorted([q for q in ks if abs(q["y"] - yl) <= 1.0], key=lambda q: q["xn"])) or None


def halves(page):
    """左右の半分に分ける。右半分のずれは 411 前後だが、ページによって 0.6 ほど違う（修正 🤍20261117-3：
    調教師の欄の境界 236 で右半分の約3分の1の行が崩れていた）。そこで半分ごとに枠番（1桁の数字）の x の最頻値を測り、
    それが WAKU_X に来るようにずらす。枠番が見つからない半分（見出しだけの半分など）は既定のずれを使う。"""
    out = {0: [], 1: []}
    for q in page["pieces"]:
        out[1 if q["x"] >= HALF else 0].append(q)
    res = {}
    for side, ps in out.items():
        base = RIGHT_X0 if side else 0.0
        wx = Counter(round(q["x"] - base, 1) for q in ps if 18 <= q["x"] - base < 23 and re.fullmatch(r"\d", N(q["t"])))
        off = base + ((wx.most_common(1)[0][0] - WAKU_X) if wx else 0.0)
        res[side] = [dict(q, xn=q["x"] - off, tn=N(q["t"])) for q in ps]
    return res


def line(ps, y0, tol=2.5):
    return "".join(q["tn"] for q in sorted([q for q in ps if abs(q["y"] - y0) <= tol], key=lambda q: q["xn"]))


def header_of(ps):
    cand = []
    for q in ps:
        if "競走" in q["tn"]:
            t = line(ps, q["y"])
            if re.match(r"\d{5}\d{1,2}月\d{1,2}日", t):
                cand.append((q["y"], t))
    if not cand:
        return None
    y0, text = max(cand)
    out = {"header_raw": text, "header_y": y0}
    m = re.match(r"(\d{5})(\d{1,2})月(\d{1,2})日", text)
    out["heading_no"], out["month"], out["day"] = m.group(1), int(m.group(2)), int(m.group(3))
    m = re.search(r"\((\d{4})年(\d+)(\D+?)\)", text)
    out["year"], out["kai"], out["place"] = (int(m.group(1)), int(m.group(2)), m.group(3)) if m else (None, None, None)
    m = re.search(r"第(\d+)日", text); out["nichi"] = int(m.group(1)) if m else None
    m = re.search(r"第(\d+)競走", text); out["race_no"] = int(m.group(1)) if m else None
    m = re.search(r"(\d),(\d{3})(?!\d)", text)
    out["distance_m"] = int(m.group(1) + m.group(2)) if m else None
    out["distance_source"] = "見出しの距離（v2抽出）" if m else None
    # 天候・馬場（見出し行の左、または少し上）
    wg_txt = "".join(q["tn"] for q in sorted([q for q in ps if -3 <= q["y"] - y0 <= 9 and q["xn"] < 140], key=lambda q: (-q["y"], q["xn"])))
    gt = re.search(r"\(芝\)(良|稍重|重|不良)", wg_txt + text); gd = re.search(r"\(ダート\)(良|稍重|重|不良)", wg_txt + text)
    if gt and gd:
        # 障害で芝・ダートの両方を使う競走は馬場を分けて書く（「(芝)良」は見出しより少し上の行にあることがある）
        wm = re.search(r"(晴|曇|小雨|雨|小雪|雪)", wg_txt + text)
        out.update(weather=wm.group(1) if wm else None, going=None, going_turf=gt.group(1), going_dirt=gd.group(1))
    else:
        m = re.search(r"(晴|曇|小雨|雨|小雪|雪)(良|稍重|重|不良)", wg_txt + text)
        out.update(weather=m.group(1) if m else None, going=m.group(2) if m else None, going_turf=None, going_dirt=None)
    below = "".join(q["tn"] for q in sorted([q for q in ps if 0 < y0 - q["y"] <= 14], key=lambda q: (-q["y"], q["xn"])))
    m = re.search(r"発走(\d+)時(\d+)分", below); out["post_time_scheduled"] = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}" if m else None
    m = re.search(r"\((芝|ダート|障害)[^)]*\)", below)
    out["surface_raw"] = m.group(0) if m else None
    out["surface"] = m.group(1) if m else None
    # 障害の判定（修正 🤍20261117-3）：平地は必ず回り（右・左・直線）を書く。回りのない表記（(芝)・(芝・ダート)・(芝・外)・(芝・外内) など）は障害。
    # 見出しの「障害」「ジャンプ」「J・G」も障害の根拠にする（ペガサスジャンプS・中山GJ が「(芝・外)」で芝に入っていた）
    no_turn = bool(out["surface_raw"]) and not re.search(r"右|左|直線", out["surface_raw"])
    words = [w for w in ("障害", "ジャンプ", "J・G") if w in text]
    out["surface_detail"] = out["surface_raw"]
    if no_turn or words:
        out["surface"] = "障害"
        out["surface_basis"] = "; ".join((["回りの記載がない（障害の表記）"] if no_turn else []) + [f"見出しに『{w}』" for w in words])
    else:
        out["surface_basis"] = "回りの記載あり（平地）"
    alltxt = "".join(q["tn"] for q in ps)
    m = re.search(r"\((\d+)頭\)", alltxt); out["field_size"] = int(m.group(1)) if m else None
    return out


def num(s):
    return re.sub(r"[^\d]", "", s or "")


def horses_of(ps, y0):
    body = [q for q in ps if q["y"] < y0 - 15]
    anchors = []
    rows = defaultdict(list)
    for q in body:
        rows[round(q["y"] * 2) / 2].append(q)
    for y, qs in rows.items():
        wk = [q for q in qs if col_of(q["xn"]) == "waku" and re.fullmatch(r"[1-8]", q["tn"])]
        ub = [q for q in qs if col_of(q["xn"]) == "umaban" and re.fullmatch(r"\d", q["tn"])]
        nm = [q for q in body if abs(q["y"] - y) <= 5 and col_of(q["xn"]) == "name" and re.fullmatch(r"[ァ-ヶー・ヴ]+", q["tn"])]
        if len(wk) == 1 and ub and nm:
            anchors.append({"y": y, "waku": int(wk[0]["tn"]), "umaban": int("".join(q["tn"] for q in sorted(ub, key=lambda q: q["xn"])))})
    anchors.sort(key=lambda a: -a["y"])
    if not anchors:
        return []
    lowest = anchors[-1]["y"] - 8
    cells = [defaultdict(list) for _ in anchors]
    for q in body:
        c = col_of(q["xn"])
        if q["y"] < lowest or c in (None, "waku", "umaban"):
            continue
        i = min(range(len(anchors)), key=lambda k: abs(anchors[k]["y"] - q["y"]))
        dy = q["y"] - anchors[i]["y"]
        # 同じ行に属する欄は ±2.5。名前・騎手名・馬主などは2段になるので ±6 まで。減量前の重量は行と行の間にあり使わない
        lim = 2.5 if c in ("carried", "body", "body_diff", "time", "odds", "pop", "sexage") else 6
        if c == "base_weight":
            # 減量前の重量は行と行の間（約3pt上）にあり使わない。ただし 57.5 の「5」のように、斤量の小数部は
            # 行と同じ高さでこの欄に入る（修正 🤍20261117-3：小数部が落ちて 57 になっていた）
            if abs(dy) <= 1.0:
                cells[i]["carried_frac"].append(q)
            continue
        if abs(dy) > lim:
            continue
        cells[i][c].append(q)
    out = []
    for a, c in zip(anchors, cells):
        t = {k: "".join(q["tn"] for q in sorted(v, key=lambda q: (-round(q["y"]), q["xn"]))) for k, v in c.items()}
        h = {"waku": a["waku"], "umaban": a["umaban"], "name": name_on_row(c.get("name", []), a["y"]),
             "raw": t, "problems": [],
             # 🤍20261117-4：字形の復元のため、性齢・毛色・騎手・調教師の欄の断片をフォント名つきで残す
             "cell_pieces": {k: [{"x": q["x"], "y": q["y"], "xn": q["xn"], "f": q.get("f"), "t": q["t"]} for q in sorted(c.get(k, []), key=lambda q: q["xn"])] for k in ("sexage", "color", "mark", "jockey", "trainer")}}
        # 性齢と毛色は並びが前後する（「牝3 鹿」「鹿 牡3」）。2つの欄をつないで読む
        sa = t.get("sexage", "") + t.get("color", "")
        m = re.search(r"([牡牝セ騸])(\d+)", sa)
        h["sex"], h["age"] = (m.group(1), int(m.group(2))) if m else (None, None)
        if not m:
            h["sex_note"] = "性別の字形が読めない（{cNNN}のまま。推測しない）" if "{c" in sa else "性齢なし"
            m2 = re.search(r"\}(\d+)", sa)
            h["age"] = int(m2.group(1)) if m2 else None
        cr = t.get("carried", "")
        if cr.endswith(".") and re.fullmatch(r"\d", t.get("carried_frac", "")):
            cr += t["carried_frac"]
        mw = re.fullmatch(r"(\d{2})(\.\d)?", cr)
        cw = cr if mw else num(cr)
        if mw:
            h["weight_carried"] = float(cr) if mw.group(2) else int(cr)
        else:
            h["weight_carried"] = int(cw) if cw else None
            if cr:
                h["problems"].append(f"斤量の形が読めない {cr!r}")
        if h["weight_carried"] is not None and not (45 <= h["weight_carried"] <= 70):
            h["problems"].append(f"斤量が値域外 {cw}"); h["weight_carried"] = None
        h["jockey_mark"] = (re.findall(r"[★☆▲△◇]", t.get("mark", "") + t.get("jockey", "")) or [None])[0]
        # 修正 🤍20261117-3：馬主の2段目は x≈192.4 から始まり、境界 193 では騎手の欄に混ざっていた（例「川須栄彦・」）。境界を 191 にした
        jk = re.sub(r"[★☆▲△◇]", "", t.get("jockey", ""))
        # 短期免許・地方の騎手は「R.ムーア(英)」「(大井)」のように国名・所属を添える。名前と分けて残す（原文は raw.jockey）
        mj = re.fullmatch(r"(.+?)\(([^()]+)\)", jk)
        h["jockey"], h["jockey_affiliation_raw"] = (mj.group(1), mj.group(2)) if mj else (jk or None, None)
        h["horse_name_raw"] = t.get("name") or None
        h["trainer"] = t.get("trainer") or None
        # 馬体重・増減・タイムは隣り合う欄で、増減が2桁のとき ss.s のタイムの先頭桁が増減の欄にはみ出す（例 "+14"+"5"+"9.9"）。
        # 3つの欄をつないでから「3桁の馬体重」「増減（±0 か符号＋1〜2桁）」「タイム（m:ss.s か ss.s）」の形で分ける
        tt = t.get("time", "")
        # 「〃」は馬体重・増減と同じ断片に入ることがある（例 "8〃"）。どこにあっても同タイムの印とする
        h["ditto_time"] = "〃" in tt + t.get("body_diff", "") + t.get("body", "")
        comb = (t.get("body", "") + t.get("body_diff", "") + tt).replace("〃", "")
        # 増減の「―」は前走の馬体重がない（初出走など）の表記
        sp = split_body_time(comb) if comb else None
        h["body_weight"] = h["body_weight_diff_raw"] = h["time"] = h["time_basis"] = None
        if sp:
            bw_, diff_, time_, kind_, left = sp
            if bw_:
                h["body_weight"] = int(bw_)
                if not 300 <= h["body_weight"] <= 700:
                    h["problems"].append(f"馬体重が値域外 {bw_}"); h["body_weight"] = None
            h["body_weight_diff_raw"] = diff_
            if time_:
                h["time"] = time_ if kind_ == "m:ss.s" else "0:" + time_
                h["time_basis"] = "原本のタイム（m:ss.s）" if kind_ == "m:ss.s" else "原本のタイム（ss.s・1分未満）"
            if left:
                h["time_overflow_raw"] = left
                h["time_basis"] = (h["time_basis"] or "") + f"（欄外の数字『{left}』は着差の欄のはみ出しとみて使わない）"
                if not time_:
                    h["problems"].append(f"タイムの欄を読めない {comb}")
        elif re.fullmatch(r"[―─]+", t.get("body", "") + t.get("body_diff", "")) or "計不" in comb:
            h["body_weight_note"] = "計量なし（―・計不）"
        elif any(w in "".join(t.values()) for w in STATUS_WORDS):
            m2 = re.match(r"(\d{3})", t.get("body", ""))
            h["body_weight"] = int(m2.group(1)) if m2 else None
            h["body_weight_diff_raw"] = None
        elif comb:
            h["problems"].append(f"馬体重・増減・タイムの欄を分けられない {comb}")
        h["margin_raw"] = t.get("margin")
        m = re.search(r"(\d+)\.(\d)", t.get("odds", ""))
        h["win_odds"] = float(f"{m.group(1)}.{m.group(2)}") if m else None
        h["pop_raw"] = t.get("pop")
        allt = "".join(t.values())
        h["status_word"] = next((s for s in STATUS_WORDS if s in allt), None)
        out.append(h)
    # 行順・タイム（〃）・確定着順・状態
    prev_time, finishers = None, 0
    prev_pos = None
    for i, h in enumerate(out, 1):
        h["row_order"] = i
        if h["time"]:
            prev_time = h["time"]
        elif h["ditto_time"] and prev_time and h["status_word"] is None:
            h["time"] = prev_time; h["time_basis"] = "〃（直前の行と同タイム）"
        sw = h["status_word"]
        if sw in ("競走除外", "出走取消", "競走中止", "失格"):
            h["finish_pos"], h["row_state"] = None, sw
        elif sw == "降着":
            h["finish_pos"], h["row_state"] = None, "降着（着順は原本の表記を要確認・判読保留）"
        elif h["time"]:
            finishers += 1
            if "同着" in (h["margin_raw"] or "") and prev_pos is not None:
                h["finish_pos"], h["row_state"] = prev_pos, "同着"
            else:
                h["finish_pos"], h["row_state"] = finishers, "確定着順"
            prev_pos = h["finish_pos"]
        else:
            h["finish_pos"], h["row_state"] = None, "判読不能"
            h["problems"].append("タイムも状態語も読めない")
        h["finish_pos_basis"] = {"確定着順": "成績表の行順（完走馬の数）", "同着": "着差欄『同着』（直前の行と同じ着順）"}.get(h["row_state"], "着順なし")
    return out


def main(pieces_path, meta_path, out_path):
    pages = json.load(open(pieces_path)); meta = json.load(open(meta_path))
    races = []
    for pi, page in enumerate(pages):
        for side, ps in halves(page).items():
            h = header_of(ps)
            if not h or h.get("race_no") is None:
                continue
            hs = horses_of(ps, h.pop("header_y"))
            starters = sum(1 for x in hs if x["row_state"] not in ("競走除外", "出走取消"))
            fp = [x["finish_pos"] for x in hs if x["finish_pos"]]
            checks = {"rows": len(hs), "field_size": h["field_size"], "starters": starters,
                      "rows_eq_field_size": h["field_size"] in (len(hs), starters),
                      "umaban_unique": len({x["umaban"] for x in hs}) == len(hs),
                      "umaban_cover": sorted(x["umaban"] for x in hs) == list(range(1, len(hs) + 1)),
                      "names_present": all(x["name"] for x in hs),
                      "no_unreadable_rows": all(x["row_state"] != "判読不能" for x in hs),
                      "no_value_problems": all(not x["problems"] for x in hs),
                      "finish_pos_consistent": fp == sorted(fp) and (not fp or fp[0] == 1)}
            ft = [(x["finish_pos"], x["time"]) for x in hs if x["finish_pos"] and x["time"]]
            def secs(t_):
                mm, ss = t_.split(":"); return int(mm) * 60 + float(ss)
            checks["times_non_decreasing"] = all(secs(ft[i][1]) <= secs(ft[i + 1][1]) for i in range(len(ft) - 1))
            checks["verified"] = all(checks[k] for k in checks if k not in ("rows", "field_size", "starters"))
            races.append(dict(h, page=pi + 1, side="左" if side == 0 else "右", horses=hs, checks=checks,
                              source_url=meta["url"], source_sha256=meta["sha256"], observed_at=meta["acquired_at"], published_at=None))
    races.sort(key=lambda r: r["race_no"])
    json.dump({"source": meta, "races": races}, open(out_path, "w"), ensure_ascii=False, indent=1)
    print(meta["url"].rsplit("/", 1)[1], "races", len(races), "verified", sum(r["checks"]["verified"] for r in races))


if __name__ == "__main__":
    main(*sys.argv[1:4])
