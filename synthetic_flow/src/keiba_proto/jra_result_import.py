"""JRA公開「レース成績」PDF（1レース分）の取込み変換（🤍20261115-13）。通信しない。抽出済みの断片（tools/pdf_text.py の出力）を受け取る。
方針：
- 原本優先。値は原本の文字として読めたものだけを使い、読めない字形は推測しない（{cNNN} の字形トークンのまま保持）。
- 払戻欄の組番は特殊字形で、字形から馬番を決めない。着順表の普通の数字から組番を導き、払戻欄の字形トークンの並びが
  それと矛盾しない場合だけ「照合済み」とする。同着・取消・欠損・不整合は保留にする。
- 発走は「表記（分単位）」として持ち、秒精度の実発走とはしない。発表日時は原本に無いので持たない（None）。
- 結果資料なので、発走前の入力（オッズ・特徴）へは流さない。System へは払戻行だけを渡す（予想・精算は作らない）。
"""
import re, json, hashlib, copy, datetime

BETS = ["単勝", "複勝", "枠連", "馬連", "馬単", "ワイド", "3連複", "3連単"]
_NORM = str.maketrans({"⾺": "馬", "⾛": "走", "⽇": "日", "⽉": "月", "，": ",", "−": "-", "．": ".", "：": ":", "＋": "+", "（": "(", "）": ")"})


def norm(t):
    return t.translate(_NORM)


def is_glyph(p):
    return p["t"].startswith("{c") and p["f"].startswith("T1_")


def race_block(pieces, race_label):
    """race_label（例 '30025'）の見出しから次の見出しまで（同じ半ページ）を返す。"""
    starts = [i for i, p in enumerate(pieces) if re.fullmatch(r"3\d{4}", p["t"])]
    i0 = next(i for i in starts if pieces[i]["t"] == race_label)
    nxt = [i for i in starts if i > i0]
    return pieces[i0:nxt[0] if nxt else len(pieces)]


def _row_text(ps):
    return norm("".join(p["t"] for p in sorted(ps, key=lambda p: p["x"])))


def parse_race(pieces, race_label):
    blk = race_block(pieces, race_label)
    head = norm("".join(p["t"] for p in blk[:40]))
    out = {"race_label": race_label, "issues": []}
    m = re.search(r"(\d{1,2})月(\d{1,2})日", head)
    y = re.search(r"(\d{4})年(\d+)(東京|中山|京都|阪神|中京|新潟|福島|小倉|札幌|函館)", head)
    nd = re.search(r"第(\d+)日第(\d+)競走", head)
    post = re.search(r"発走(\d{1,2})時(\d{2})分", head)
    out["date"] = "%s-%02d-%02d" % (y.group(1), int(m.group(1)), int(m.group(2))) if (m and y) else None
    out["kai"], out["venue"] = (int(y.group(2)), y.group(3)) if y else (None, None)
    out["nichi"], out["race_no"] = (int(nd.group(1)), int(nd.group(2))) if nd else (None, None)
    out["post_label"] = "%02d:%s" % (int(post.group(1)), post.group(2)) if post else None
    out["post_label_note"] = "原本の発走表記（分単位・予定）。実発走の時刻ではない"
    out["announced_at"] = None          # 原本に発表日時の記載なし
    fs = [p for p in blk if re.fullmatch(r"（\d+頭）", p["t"])]
    out["field_size"] = int(re.search(r"\d+", fs[0]["t"]).group()) if fs else None
    y_fs = fs[0]["y"] if fs else None
    # 成績表の行：枠番（x≈21.0）・馬番（x≈21.9〜22.2）が同じ y にある行。上から下の順に並べる
    ys = sorted({p["y"] for p in blk if abs(p["x"] - 21.0) < 0.3 and re.fullmatch(r"\d", p["t"])
                 and (y_fs is None or p["y"] > y_fs)}, reverse=True)
    rows = []
    for yy in ys:
        rp = [p for p in blk if abs(p["y"] - yy) < 0.8]
        waku = [p["t"] for p in rp if abs(p["x"] - 21.0) < 0.3]
        uma = [p["t"] for p in rp if 21.5 < p["x"] < 23.0 and re.fullmatch(r"\d{1,2}", p["t"])]
        name = [p["t"] for p in rp if abs(p["x"] - 49.5) < 0.5]
        tm = _row_text([p for p in rp if 330 < p["x"] < 345 and not is_glyph(p)])
        if not tm and any(p["t"] == "〃" and 312 < p["x"] < 316 for p in rp):
            tm = "〃"                      # 前の馬と同タイム（着差欄は別。同着を意味しない）
        od = _row_text([p for p in rp if 384 < p["x"] < 393 and not is_glyph(p)])
        rows.append({"waku": int(waku[0]) if waku else None, "umaban": int(uma[0]) if uma else None,
                     "name": name[0] if name else None, "time": tm or None, "odds": float(od) if re.fullmatch(r"\d+\.\d", od) else None})
    for i, r in enumerate(rows):
        r["order_by_row"] = i + 1      # 着順の数字は原本に無い。行の並び（上から）を着順とし、下の検査で裏づける
    out["rows"] = rows
    ann = _annotations(blk, ys)                 # JR13-04／JR15-04：同着・降着などの注記（領域・列・文脈で分類）
    out["annotations"] = ann["asserted"]
    out["annotation_uncertain"] = ann["uncertain"]
    out["annotation_ignored"] = ann["ignored"]
    out["annotation_spans"] = ann["spans"]
    out["issues"] = ["注記：%s" % w for w in out["annotations"]] + ["注記の可能性：%s" % w for w in out["annotation_uncertain"]]
    # 払戻
    i_pay = next((i for i, p in enumerate(blk) if norm(p["t"]).startswith("払戻")), None)
    i_vote = next((i for i, p in enumerate(blk) if p["t"] == "票数"), None)
    seg = blk[i_pay:i_vote] if i_pay is not None else []
    # 券種名と値の y が少しずれる（0.4程度）ので、1.0 以内を同じ行にまとめてから x の順に並べる
    rowy = []
    for p in sorted(seg, key=lambda p: -p["y"]):
        if not rowy or rowy[-1] - p["y"] > 1.0:
            rowy.append(p["y"])
    line = lambda p: min(range(len(rowy)), key=lambda i: abs(rowy[i] - p["y"]))
    pay = sorted(seg, key=lambda p: (line(p), p["x"]))
    out["payouts"] = _parse_payouts(pay)
    return out


def _parse_payouts(pay):
    res = {b: [] for b in BETS}
    cur, toks, amt = None, None, ""
    def flush():
        nonlocal toks, amt
        if cur and amt:
            m = re.fullmatch(r"(?:\((\d)-(\d)\))?([\d,]+)円", amt)
            if m:
                ent = {"tokens": toks or [], "yen_text": amt, "yen": int(m.group(3).replace(",", ""))}
                if m.group(1):
                    ent["waku_combo"] = [int(m.group(1)), int(m.group(2))]
                res[cur].append(ent)
            else:
                res[cur].append({"tokens": toks or [], "yen_text": amt, "yen": None, "issue": "金額の形式が不正"})
        toks, amt = None, ""
    for p in pay:
        t = norm(p["t"])
        if t in BETS:
            flush(); cur = t; continue
        if cur is None:
            continue
        if is_glyph(p):
            if amt:
                flush()
            toks = (toks or []) + re.findall(r"\{c\d+\}", t)
            continue
        amt += t.replace(" ", "")
        if amt.endswith("円"):
            flush()
    flush()
    return res


ANNOTATION_WORDS = ("同着", "降着", "失格", "競走中止", "中止", "取消", "除外", "審議", "着順変更", "繰上", "入線")


# JR14-04：同じ表示行で隣り合う断片をつないで注記を探す。この抽出器の座標で、同じ語の続きの断片の間隔は実例1Rで最大3.6、
# 別の欄どうしは最小10.3だったので、その間の 8.0 以下を「隣接」とする（実例1件からの値。別の配置では要確認）
LINE_DY = 0.8
ADJ_DX = 8.0


def _line_runs(blk):
    """同じ行（y の差が LINE_DY 未満）で、x の間隔が ADJ_DX 以下の断片をつないだ文字列の一覧。"""
    lines = []
    for p in sorted(blk, key=lambda p: (-p["y"], p["x"])):
        if lines and abs(lines[-1][0]["y"] - p["y"]) < LINE_DY:
            lines[-1].append(p)
        else:
            lines.append([p])
    runs = []
    for ln in lines:
        ln = sorted(ln, key=lambda p: p["x"])
        cur = [ln[0]]
        for a, b in zip(ln, ln[1:]):
            if b["x"] - a["x"] <= ADJ_DX:
                cur.append(b)
            else:
                runs.append(cur); cur = [b]
        runs.append(cur)
    return [norm("".join(p["t"] for p in r)) for r in runs if len(r) > 1]


# JR15-04：成績表の列（実例1Rの x 座標から。抽出器の座標系）。固有名の列の一致は注記としない（例：生産者名「入線牧場」）
NAME_COLUMNS = ((40.0, 105.0, "馬名"), (150.0, 186.0, "騎手"), (186.0, 230.0, "馬主"), (230.0, 262.0, "調教師"), (262.0, 305.0, "生産者"))
RESULT_COLUMNS = ((320.0, 382.0, "タイム・着差"),)
NEGATIONS = ("なし", "無し")
# 本人の方針（同着は記載がある時だけ報告。記載がなければ同着なし。明確な否定は否定、曖昧な記載は保留）に基づく、明示否定の閉じた文型：
#   〈注記の語〉＋（区切り「:」「：」・空白を0個以上）＋「なし」「無し」＋（文字列の終わり、または終端記号）
# 「なし」の後に終端記号以外が続く文（例「同着なしとは断定できない」「同着なしではない」）は明示否定とせず、曖昧として保留する
NEG_SEPARATORS = ":： \u3000"
NEG_TERMINATORS = "、。，．,./／・）)」』]】 \u3000\n;；"


def _column(x):
    for lo, hi, name in NAME_COLUMNS:
        if lo <= x < hi:
            return "name", name
    for lo, hi, name in RESULT_COLUMNS:
        if lo <= x < hi:
            return "result", name
    return "other", None


def _annotations(blk, row_ys=None):
    """JR13-04／JR14-04／JR15-04／JR16-03〜05：例外的な成績を示す注記（同着・降着など）を探し、出現ごとに3つに分ける。
    - 断片は、同じ行で x の間隔が ADJ_DX 以下のものをつないだ「連」にまとめる。各断片はちょうど1つの連に属するので、
      単体の断片と連結の文字列を二重に評価しない（JR16-04：「同着」「なし」の分割を、単体の「同着」として肯定にしない）
    - 連の中の、注記の語の「すべての出現」を位置つきで調べる。重なる語は長い方だけを使う（「競走中止」と「中止」など）（JR16-03）
    - 否定（直後が「なし」「無し」）は、その出現だけを修飾する。後続の別の肯定の出現は落とさない（JR16-03）
    - 出現が覆う断片すべての列を見る（JR16-05）。すべてが同じ固有名の列→注記としない。成績表の外→注記。すべてがタイム・着差の列→注記。
      それ以外（その他の列、列をまたぐもの）→断定せず「注記の可能性」で保留
    戻り値：{"asserted": [...], "uncertain": [...], "ignored": [...], "spans": [...]}。spans は出現ごとの位置・列・分類（記録用）"""
    out = {"asserted": [], "uncertain": [], "ignored": [], "spans": []}
    ps = [p for p in blk if not is_glyph(p)]
    lo = min(row_ys) - LINE_DY if row_ys else None
    hi = max(row_ys) + LINE_DY if row_ys else None
    for run in _runs(ps):
        text, owner = "", []
        for k, p in enumerate(run):
            t = norm(p["t"])
            text += t
            owner += [k] * len(t)
        found = []
        for w in ANNOTATION_WORDS:
            i = text.find(w)
            while i >= 0:
                found.append((i, i + len(w), w))
                i = text.find(w, i + 1)
        found = [m for m in found if not any(o != m and o[0] <= m[0] and m[1] <= o[1] and (o[1] - o[0]) > (m[1] - m[0]) for o in found)]
        for i0, i1, w in sorted(found):
            pieces = [run[k] for k in sorted(set(owner[i0:i1]))]
            in_table = lo is not None and all(lo <= p["y"] <= hi for p in pieces)
            cols = [_column(p["x"]) for p in pieces] if in_table else []
            neg, used = _negation(text[i1:])
            if neg == "ambiguous" and in_table:
                q = i1 + used
                # A separately positioned, wholly numeric weight field is not prose.
                # Do not treat arbitrary symbols, questions, or column boundaries as endings.
                if q < len(text) and owner[q] != owner[q - 1]:
                    tail = run[owner[q]:]
                    if all(305.0 <= p["x"] < 320.0 and re.fullmatch(r"[0-9＋+−\-]+", norm(p["t"])) for p in tail):
                        neg = "clear"
            if in_table and len(set(cols)) == 1 and cols[0][0] == "name" and all(_column(p["x"]) == cols[0] for p in run):
                cls, why = "ignored", "固有名の列（%s）" % cols[0][1]
            elif neg == "clear":
                cls, why = "ignored", "明示否定（%s）" % text[i0:min(len(text), i1 + 6)]
            elif neg == "ambiguous":
                cls, why = "uncertain", "曖昧な否定（%s）" % text[i0:min(len(text), i1 + 12)]
            elif not in_table:
                cls, why = "asserted", "成績表の外"
            elif len(set(cols)) == 1 and cols[0][0] == "name" and all(_column(p["x"]) == cols[0] for p in run):
                cls, why = "ignored", "固有名の列（%s）" % cols[0][1]
            elif all(c[0] == "result" for c in cols):
                cls, why = "asserted", "タイム・着差の列"
            else:
                cls, why = "uncertain", "列をまたぐ" if len(set(cols)) > 1 else "その他の列"
            out["spans"].append({"word": w, "text": text, "span": [i0, i1], "x": [p["x"] for p in pieces], "y": pieces[0]["y"],
                                 "columns": [c[1] or c[0] for c in cols] or ["表の外"], "class": cls, "why": why})
            if cls == "ignored":
                ent = "%s：%s" % (w, why)
                if ent not in out["ignored"]:
                    out["ignored"].append(ent)
            elif w not in out[cls]:
                out[cls].append(w)
    # Preserve undecodable annotation candidates instead of deleting their glyphs.
    # This detects partial known annotation vocabulary + unknown glyph, not all OCR omissions.
    for run in _runs(blk):
        raw = "".join(norm(p["t"]) for p in run)
        if any(is_glyph(p) for p in run):
            before = raw.split("{c", 1)[0]
            if any(before.strip() == word[:n] for word in ANNOTATION_WORDS for n in range(1, len(word)+1)):
                if "読取不能の注記候補" not in out["uncertain"]:
                    out["uncertain"].append("読取不能の注記候補")
    out["uncertain"] = [w for w in out["uncertain"] if w not in out["asserted"]]
    return out


def _closed_negation_tail(after):
    tail = after.lstrip(NEG_TERMINATORS)
    if not tail:
        return True
    # Permit an explicit list of complete negative annotation clauses only.
    if tail == after:
        return False
    for word in sorted(ANNOTATION_WORDS, key=len, reverse=True):
        if tail.startswith(word):
            rest = tail[len(word):].lstrip(NEG_SEPARATORS)
            for neg in NEGATIONS:
                if rest.startswith(neg):
                    return _closed_negation_tail(rest[len(neg):])
    return False


def _negation(rest):
    """注記の語の直後の文字列 rest が、明示否定の閉じた文型か。
    戻り値：(判定, 読んだ長さ)。判定は "clear"（明示否定）・"ambiguous"（なし/無しの後に終端以外が続く）・None（否定ではない）"""
    t = rest.lstrip(NEG_SEPARATORS)
    sep = len(rest) - len(t)
    for n in NEGATIONS:
        if t.startswith(n):
            after = t[len(n):]
            return ("clear" if _closed_negation_tail(after) else "ambiguous"), sep + len(n)
    return None, 0


def _ja_letter(c):
    """ひらがな・カタカナ・漢字か（日本語の文の続きになりうる文字か）"""
    o = ord(c)
    return 0x3041 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or c in "々〆ー"


def _runs(ps):
    """同じ行（y の差が LINE_DY 未満）で、x の間隔が ADJ_DX 以下の断片をつないだ連（断片のリスト）の一覧。1断片だけの連も含む。"""
    lines = []
    for p in sorted(ps, key=lambda p: (-p["y"], p["x"])):
        if lines and abs(lines[-1][0]["y"] - p["y"]) < LINE_DY:
            lines[-1].append(p)
        else:
            lines.append([p])
    runs = []
    for ln in lines:
        ln = sorted(ln, key=lambda p: p["x"])
        cur = [ln[0]]
        for a, b in zip(ln, ln[1:]):
            if b["x"] - a["x"] <= ADJ_DX:
                cur.append(b)
            else:
                runs.append(cur); cur = [b]
        runs.append(cur)
    return runs


def _date_ok(d):
    if not (isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)):
        return False
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        return False
    return True


def _small_int(v, hi=12):
    return type(v) is int and 1 <= v <= hi


VENUES = ("東京", "中山", "京都", "阪神", "中京", "新潟", "福島", "小倉", "札幌", "函館")


def identity_issues(rec):
    """JR14-05：開催・競走の識別子（開催日・場・回・日・競走番号・見出し番号）と型を、変換・キー作成の前に検査する。
    問題の一覧を返す（空なら正常）。bool は整数として扱わない。"""
    bad = []
    if not _date_ok(rec.get("date")):
        bad.append("開催日")
    if rec.get("venue") not in VENUES:
        bad.append("場")
    for f, name in (("kai", "回"), ("nichi", "日"), ("race_no", "競走番号")):
        if not _small_int(rec.get(f)):
            bad.append(name)
    lab = rec.get("race_label")
    if not (isinstance(lab, str) and re.fullmatch(r"\d{5}", lab)):
        bad.append("見出し番号")
    return bad


# 券種ごとの構造（行数・要素数・異なる馬の数）。複勝・ワイドの行数は頭数による（8頭以上を対象とし、それ未満は保留）
SHAPE = {"単勝": (1, 1), "馬連": (1, 2), "馬単": (1, 2), "3連複": (1, 3), "3連単": (1, 3), "複勝": (3, 1), "ワイド": (3, 2)}


def _bet_shape_ok(bet, ents):
    rows_n, k = SHAPE[bet]
    if len(ents) != rows_n:
        return False
    for e in ents:
        t = e.get("tokens") or []
        if len(t) != k or len(set(t)) != k:
            return False
    if bet in ("複勝",) and len({e["tokens"][0] for e in ents}) != rows_n:
        return False
    if bet == "ワイド" and len({frozenset(e["tokens"]) for e in ents}) != rows_n:
        return False
    return True


def check_and_resolve(rec):
    """検査と組番の導出。結果は rec['checks']・rec['resolved']・rec['holds'] に入れる（rec は複製して返す）。
    JR13-01〜04：必要な行・トークン・写像・独立対照・注記を、参照する前にすべて検査する。不整合は例外にせず理由つきで保留する。
    - 核の検査（行・タイム・単勝オッズ・枠連の数字・注記・単勝／複勝／馬単／3連複の構造と整合）が1つでも成り立たなければ、
      組番は1つも導出しない（全体を保留）
    - 核が成り立つとき、他の券種（馬連・ワイド・3連単・枠連）は券種ごとに構造を検査し、不正な券種だけを保留する
    """
    try:
        return _check_and_resolve(rec)
    except Exception as ex:            # 最後の防御：想定外の構造でも例外を外へ出さず保留にする
        r = copy.deepcopy(rec)
        r["checks"], r["resolved"] = {"internal_error": repr(ex)}, {}
        r["holds"] = ["入力の構造が想定外のため保留（%s）" % type(ex).__name__]
        return r


def _check_and_resolve(rec):
    r = copy.deepcopy(rec)
    rows, pays = r.get("rows") or [], r.get("payouts") or {}
    checks, holds, res = {}, [], {}
    ident = identity_issues(r)
    checks["identity_valid"] = not ident
    if ident:
        holds.append("開催・競走の識別子が欠損・型不正（%s）：組番を導出しない" % "・".join(ident))
    fs = r.get("field_size")
    checks["field_size_supported"] = isinstance(fs, int) and fs >= 8
    checks["field_size_matches_rows"] = fs == len(rows) and len(rows) >= 3
    nums = [x.get("umaban") for x in rows]
    checks["umaban_unique_complete"] = bool(rows) and None not in nums and sorted(nums) == list(range(1, len(rows) + 1))
    def secs(t):
        m = re.fullmatch(r"(\d):(\d{2})\.(\d)", t or "")
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 10 if m else None
    last, ok = None, bool(rows)
    for x in rows:
        t = x.get("time") or ""
        v = last if t.startswith("〃") else secs(t[:6])
        if v is None or (last is not None and v < last):
            ok = False
        last = v
    checks["times_non_decreasing"] = ok
    ann = list(r.get("annotations") or [])
    unc = list(r.get("annotation_uncertain") or [])
    checks["no_exceptional_annotation"] = not ann
    checks["no_uncertain_annotation"] = not unc
    if ann:
        holds.append("例外的な成績の注記（%s）があり、行の並びから着順を決めない" % "・".join(ann))
    if unc:
        holds.append("注記の可能性（%s）が成績表の列・文脈から判断できない：注記と断定せず、着順も決めない" % "・".join(unc))
        ann = ann + unc
    rows_ok = checks["field_size_supported"] and checks["field_size_matches_rows"] and checks["umaban_unique_complete"] and ok
    win, wk = pays.get("単勝", []), pays.get("枠連", [])
    checks["win_yen_equals_winner_odds_x100"] = bool(rows_ok and len(win) == 1 and rows[0].get("odds") is not None
                                                    and win[0].get("yen") == round(rows[0]["odds"] * 100))
    checks["wakuren_plain_digits_match_top2"] = bool(rows_ok and len(wk) == 1 and wk[0].get("waku_combo")
                                                     == sorted([rows[0].get("waku"), rows[1].get("waku")]))
    # 核の券種の構造と、着順から決まるトークン→馬番の対応（1着＝単勝、2着＝馬単の2番目、3着＝3連複の残り）
    core = {b: _bet_shape_ok(b, pays.get(b, [])) for b in ("単勝", "複勝", "馬単", "3連複")}
    checks["core_bet_shapes"] = core
    tokmap = None
    if all(core.values()):
        t1 = win[0]["tokens"][0]
        ex = pays["馬単"][0]["tokens"]
        trio = set(pays["3連複"][0]["tokens"])
        place = {e["tokens"][0] for e in pays["複勝"]}
        rest = trio - {t1, ex[1]}
        if ex[0] == t1 and t1 in trio and ex[1] in trio and len(rest) == 1 and place == trio:
            tokmap = {t1: 1, ex[1]: 2, next(iter(rest)): 3}
    checks["glyph_tokens_consistent"] = tokmap is not None
    if not checks["field_size_supported"]:
        holds.append("頭数が未対応（8頭未満）または不明：組番を導出しない")
    if not (checks["field_size_matches_rows"] and checks["umaban_unique_complete"] and ok):
        holds.append("成績表の行・馬番・タイムの検査が成り立たない：着順を決めない")
    if rows_ok and not checks["win_yen_equals_winner_odds_x100"]:
        holds.append("独立対照の不一致：単勝払戻と1着の単勝オッズが合わない")
    if rows_ok and not checks["wakuren_plain_digits_match_top2"]:
        holds.append("独立対照の不一致：枠連の原本数字と1・2着の枠が合わない")
    if len(win) > 1:
        holds.append("単勝が1件でない（同着の可能性）：組番を確定しない")
    for b in ("単勝", "複勝", "馬単", "3連複"):
        if not pays.get(b):
            holds.append("払戻の欠損：%s" % b)
    if not all(core.values()):
        holds.append("核の券種の構造が不正（%s）" % "・".join(b for b, v in core.items() if not v))
    elif tokmap is None:
        holds.append("払戻欄の字形トークンの並びが着順と整合しない")
    core_ok = (not ident and rows_ok and not ann and checks["win_yen_equals_winner_odds_x100"] and checks["wakuren_plain_digits_match_top2"]
               and tokmap is not None)
    if not core_ok:
        holds.append("核の検査が成り立たないため、組番を1つも導出しない")
    else:
        order = [rows[0]["umaban"], rows[1]["umaban"], rows[2]["umaban"]]
        num = {t: order[pos - 1] for t, pos in tokmap.items()}
        for bet in BETS:
            ents = pays.get(bet, [])
            if not ents:
                holds.append("払戻の欠損：%s" % bet); continue
            if any(e.get("yen") is None or e["yen"] % 10 for e in ents):
                holds.append("金額の形式・単位が不正（10円単位でない等）：%s" % bet); res[bet] = []; continue
            if bet == "枠連":
                res[bet] = [{"combo": ents[0]["waku_combo"], "yen": ents[0]["yen"], "basis": "原本の数字（1・2着の枠と一致）"}]
                continue
            if not _bet_shape_ok(bet, ents) or any(t not in num for e in ents for t in e["tokens"]):
                holds.append("券種の構造が不正（要素数・重複・行数）：%s" % bet); continue
            if bet == "ワイド" and {frozenset(e["tokens"]) for e in ents} != {frozenset(c) for c in ((a, b) for a in tokmap for b in tokmap if tokmap[a] < tokmap[b])}:
                holds.append("ワイドの3組が上位3頭の全組と一致しない"); continue
            if bet == "3連単" and [tokmap[t] for t in ents[0]["tokens"]] != [1, 2, 3]:
                holds.append("3連単の並びが着順と一致しない"); continue
            if bet == "馬連" and {tokmap[t] for t in ents[0]["tokens"]} != {1, 2}:
                holds.append("馬連の組が1・2着と一致しない"); continue
            ordered = bet in ("馬単", "3連単")
            res[bet] = [{"combo": [num[t] for t in e["tokens"]] if ordered else sorted(num[t] for t in e["tokens"]),
                         "yen": e["yen"], "basis": "着順表から導出（払戻欄の字形トークンと整合）"} for e in ents]
    checks["glyph_detail"] = {"core_bet_shapes": core, "token_positions": tokmap}
    r["checks"], r["holds"], r["resolved"] = checks, holds, res
    return r


class ImportLedger:
    """JR13-05／JR14-01／JR14-02：再取込の台帳。状態は pending（予約）・held（検査で保留）・rejected（保存先が拒否・隔離）・committed（確定）。
    - admit：未登録・held・rejected なら予約して True。確定済みなら False。予約中なら、保存先 store が渡されたときだけ保存先を照合し、
      払戻行と後処理の完了印が両方そろっていれば確定して False、そろっていなければ未完の分を補う再試行を許して True。
      store が無いと保存の成否を確かめられないので False（重複を作らない側に倒す。旧APIの契約。JD13-18）
    - commit：保存先で「行あり＋後処理済み」を確かめてから確定する。確かめられなければ False（確定しない）"""
    def __init__(self):
        self.state = {}

    def admit(self, source_sha256, race_key, store=None):
        k = (source_sha256, race_key)
        st = self.state.get(k)
        if st == "committed":
            return False
        if st == "pending":
            if store is None:
                return False
            if _store_status(store, source_key(source_sha256, race_key)) == "complete":
                self.state[k] = "committed"
                return False
            return True
        self.state[k] = "pending"
        return True

    def mark(self, source_sha256, race_key, state):
        """held・rejected を記録する（確定済みは変えない）"""
        k = (source_sha256, race_key)
        if self.state.get(k) != "committed":
            self.state[k] = state

    def commit(self, source_sha256, race_key, store):
        k = (source_sha256, race_key)
        if _store_status(store, source_key(source_sha256, race_key)) == "complete":
            self.state[k] = "committed"
            return True
        return False

    def status(self, source_sha256, race_key):
        return self.state.get((source_sha256, race_key))


def source_key(source_sha256, race_key_):
    return "%s|%s" % (source_sha256, race_key_)


def _store_status(store, skey):
    """保存先の状態：'absent'（行なし）・'row_saved'（行あり・後処理未完）・'complete'（行あり・後処理済み）。
    保存先が後処理の完了印を持たない旧形式なら、行の有無だけで判断する（その場合は後処理の完了を保証しない）。"""
    if hasattr(store, "payout_ingest_status"):
        return store.payout_ingest_status(skey)
    return "complete" if _stored(store, skey) else "absent"


def _stored(store, skey):
    return any(b.get("kind") == "payout" and b.get("source_key") == skey for b in store.body.rows())


def import_once(ledger, store, rec, source_sha256, saved):
    """取込み1回分：予約 → 払戻行の保存と後処理 → 保存先の照合で確定。途中で失敗したら予約のまま残り、同じ store を渡した再試行で
    未完の分だけ回復する（保存済みなら保存し直さない）。
    戻り値：'imported'（確定を確かめた）・'duplicate'（確定済み）・'held'（検査で保留。識別子の不正を含む）・
    'rejected'（保存先が拒否・隔離・保留した）・'incomplete'（保存先で確定を確かめられない）。imported 以外は成功ではない。"""
    # JR15-03：キーの決定・台帳の照合より前に、開催・競走の識別子をすべて検査する（完了済みキーの再配送でも同じ）
    if identity_issues(rec):
        return "held"
    rk = race_key(rec)
    if rk is None:
        return "held"
    if not ledger.admit(source_sha256, rk, store):
        return "duplicate"
    ev = to_payout_event(rec, saved, source_sha256)
    if ev is None:
        ledger.mark(source_sha256, rk, "held")
        return "held"
    out = store.ingest_row(ev)
    if isinstance(out, dict) and out.get("status") == "incomplete":
        return "incomplete"   # JR16-02：後処理の段階が未完。台帳は予約のまま（再試行で補う）
    if not isinstance(out, dict) or out.get("status") not in ("stored", "recovered", "duplicate"):
        ledger.mark(source_sha256, rk, "rejected")
        return "rejected"
    if not ledger.commit(source_sha256, rk, store):
        return "incomplete"
    return "imported"


def race_key(rec):
    """開催キー。識別子（開催日・場・回・日・競走番号・見出し番号）のどれかが不正なら None（例外にしない。JR14-05／JR15-03）"""
    if identity_issues(rec):
        return None
    return "%s/%s%02d" % (rec["date"], "R", rec["race_no"])


def to_payout_event(rec, saved, source_sha256=None):
    """System.ingest_row 用の払戻行（3連複だけを System の形式で渡す。他の券種は rec に残す）。
    JR13-01：渡された rec の resolved を信用せず、変換の直前に検査をやり直す（保留が1つでもあれば作らない）。"""
    chk = check_and_resolve(rec)
    trio = chk["resolved"].get("3連複")
    if chk["holds"] or not trio or len(trio) != 1 or race_key(chk) is None:
        return None
    rec = chk
    ev = {"kind": "payout", "race": "R%02d" % rec["race_no"], "race_key": race_key(rec), "saved": saved,
            "refund_flag": False, "refund_horses": [], "not_established": False, "special": False,
            "trio": {"combo": "-".join(map(str, trio[0]["combo"])), "pay": trio[0]["yen"]}, "source": "jra-public-pdf"}
    if source_sha256:
        ev["source_key"] = source_key(source_sha256, race_key(rec))
    return ev


def settle_synthetic(rec, tickets):
    """試験専用：合成買い目（100円単位）を照合して払戻額を計算する。予想・精算・成績には記録しない。"""
    out = []
    for t in tickets:
        ents = rec["resolved"].get(t["bet"], [])
        key = t["combo"] if t["bet"] in ("馬単", "3連単") else sorted(t["combo"])
        hit = [e for e in ents if e["combo"] == key]
        out.append(dict(t, payout=sum(e["yen"] for e in hit) * t["amount"] // 100, synthetic_test_only=True))
    return out
