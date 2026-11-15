"""JRA公開「レース成績」PDF（1レース分）の取込み変換（🤍20261115-13）。通信しない。抽出済みの断片（tools/pdf_text.py の出力）を受け取る。
方針：
- 原本優先。値は原本の文字として読めたものだけを使い、読めない字形は推測しない（{cNNN} の字形トークンのまま保持）。
- 払戻欄の組番は特殊字形で、字形から馬番を決めない。着順表の普通の数字から組番を導き、払戻欄の字形トークンの並びが
  それと矛盾しない場合だけ「照合済み」とする。同着・取消・欠損・不整合は保留にする。
- 発走は「表記（分単位）」として持ち、秒精度の実発走とはしない。発表日時は原本に無いので持たない（None）。
- 結果資料なので、発走前の入力（オッズ・特徴）へは流さない。System へは払戻行だけを渡す（予想・精算は作らない）。
"""
import re, json, hashlib, copy

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
    out["annotations"] = _annotations(blk)      # JR13-04：同着・降着などの注記
    out["issues"] = ["注記：%s" % w for w in out["annotations"]]
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


def _annotations(blk):
    """JR13-04：例外的な成績を示す注記（同着・降着など）を当該レースのブロックから拾う。見つけたら行順で着順を決めない。"""
    found = []
    for p in blk:
        t = norm(p["t"])
        for w in ANNOTATION_WORDS:
            if w in t and w not in found:
                found.append(w)
    return found


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
    checks["no_exceptional_annotation"] = not ann
    if ann:
        holds.append("例外的な成績の注記（%s）があり、行の並びから着順を決めない" % "・".join(ann))
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
    core_ok = (rows_ok and not ann and checks["win_yen_equals_winner_odds_x100"] and checks["wakuren_plain_digits_match_top2"]
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
    """JR13-05：再取込の台帳。予約（pending）と確定（committed）を分ける。
    - admit：未登録なら予約して True。確定済みなら False。予約中なら、保存先 store が渡されたときだけ保存先の安定キー
      （source_key）を照合し、保存済みなら確定して False、未保存なら再試行を許して True。store が無いと保存の成否を
      確かめられないので False（重複を作らない側に倒す。単純なキー削除はしない）
    - commit：保存先に安定キーの行があることを確かめてから確定する"""
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
            if _stored(store, source_key(source_sha256, race_key)):
                self.state[k] = "committed"
                return False
            return True
        self.state[k] = "pending"
        return True

    def commit(self, source_sha256, race_key, store):
        k = (source_sha256, race_key)
        if _stored(store, source_key(source_sha256, race_key)):
            self.state[k] = "committed"
            return True
        return False

    def status(self, source_sha256, race_key):
        return self.state.get((source_sha256, race_key))


def source_key(source_sha256, race_key_):
    return "%s|%s" % (source_sha256, race_key_)


def _stored(store, skey):
    return any(b.get("kind") == "payout" and b.get("source_key") == skey for b in store.body.rows())


def import_once(ledger, store, rec, source_sha256, saved):
    """取込み1回分：予約 → 払戻行の保存 → 保存先の照合で確定。途中で失敗したら予約のまま残り、同じ store を渡した再試行で
    未完の分だけ回復する（保存済みなら保存し直さない）。戻り値は 'imported' / 'duplicate' / 'held'。"""
    rk = race_key(rec)
    if not ledger.admit(source_sha256, rk, store):
        return "duplicate"
    ev = to_payout_event(rec, saved, source_sha256)
    if ev is None:
        return "held"
    store.ingest_row(ev)
    ledger.commit(source_sha256, rk, store)
    return "imported"


def race_key(rec):
    return "%s/%s%02d" % (rec["date"], "R", rec["race_no"])


def to_payout_event(rec, saved, source_sha256=None):
    """System.ingest_row 用の払戻行（3連複だけを System の形式で渡す。他の券種は rec に残す）。
    JR13-01：渡された rec の resolved を信用せず、変換の直前に検査をやり直す（保留が1つでもあれば作らない）。"""
    chk = check_and_resolve(rec)
    trio = chk["resolved"].get("3連複")
    if chk["holds"] or not trio or len(trio) != 1:
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
