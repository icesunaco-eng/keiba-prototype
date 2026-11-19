"""確認アプリの判定（画面に出す前の確かめ）。どれも予想を読むだけで、書き換えない。

  - pre_publish_check  公開前チェック：取消馬が買い目に残っていないか・組の形・重複・100円単位・上限・オッズの確認
  - bias_check         買い目の偏り：1頭に頼りすぎていないか、◎が入っているか
  - change_conditions  予想が変わる条件：印の境目の近さ、これから入る情報
  - horse_comment      ルールの8項目から作る短い一言（推論）
  - card_flags         一覧のカードに出す警告（取消・オッズの確認・発走済み など）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .data import horse_probs, marks_of

JST = timezone(timedelta(hours=9))
RACE_CAP = 10_000
UNIT = 100
MAX_COMBOS = 10
TP_MINUTES = {"24h": 24 * 60, "1h": 60, "15m": 15}
GROUP_LABEL = {"ability": "能力", "course": "コース", "distance": "距離", "draw": "枠", "going": "馬場",
               "pace": "展開", "recent": "近走", "weight_rider": "斤量・騎手"}
CLOSE_GAP = 0.03   # 3着内の確率の差がこれより小さければ「入れ替わりやすい」


def post_datetime(race: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{race['race_date']}T{race['post_time']}:00").replace(tzinfo=JST)
    except (KeyError, TypeError, ValueError):
        return None


def now_jst() -> datetime:
    return datetime.now(JST)


def combo_horses(combo: str) -> list[int]:
    try:
        return [int(x) for x in str(combo).split("-")]
    except ValueError:
        return []


def field_numbers(p: dict, names: dict | None = None) -> set[int]:
    nums = {int(h["n"]) for h in p.get("horses") or [] if h.get("n") is not None}
    return nums or {int(n) for n in (names or {})}


def pre_publish_check(p: dict, stakes: dict, race: dict | None = None, names: dict | None = None) -> list[dict]:
    """1行＝1項目。ok は True（合格）／False（不合格）／None（判定できない・対象なし）。"""
    out = []

    def add(ok, label, detail=""):
        out.append({"ok": ok, "label": label, "detail": detail})

    inp = p.get("inputs") or {}
    scratched = set(int(n) for n in inp.get("scratched_known") or [])
    field = field_numbers(p, names)
    if not stakes:
        add(None, "買い目", "買い目が無いので、組のチェックはしない")
    else:
        bad_scr = sorted({n for c in stakes for n in combo_horses(c)} & scratched)
        add(not bad_scr, "取消・除外の馬が買い目に無い",
            ("残っている：" + "、".join(f"{n}番" for n in bad_scr)) if bad_scr else
            (f"取消 {len(scratched)}頭はどの組にも入っていない" if scratched else "取消・除外は把握していない"))
        shape_bad = [c for c in stakes if len(set(combo_horses(c))) != 3 or combo_horses(c) != sorted(combo_horses(c))]
        out_field = sorted({n for c in stakes for n in combo_horses(c)} - field) if field else []
        add(not shape_bad and not out_field, "組の形（3頭・出走している馬番）",
            ("形がおかしい：" + "、".join(shape_bad)) if shape_bad else
            ("出走表に無い馬番：" + "、".join(map(str, out_field))) if out_field else f"{len(stakes)}組すべてOK")
        norm = ["-".join(map(str, sorted(combo_horses(c)))) for c in stakes]
        dup = len(norm) - len(set(norm))
        add(dup == 0, "同じ組の重複が無い", f"重複 {dup}組" if dup else "重複なし")
        total = sum(stakes.values())
        units_ok = all(v > 0 and v % UNIT == 0 for v in stakes.values())
        add(units_ok and total <= RACE_CAP, "100円単位・1レース上限 ¥10,000",
            f"合計 ¥{total:,}" + ("" if units_ok else "（100円単位でない組がある）"))
        add(len(stakes) <= MAX_COMBOS, f"点数が{MAX_COMBOS}点以内", f"{len(stakes)}点")
    gate = p.get("odds_gate")
    if gate is None:
        add(None, "三連複オッズの確認", "この予想はオッズを使っていない")
    else:
        add(bool(gate.get("pass")), "三連複オッズの確認",
            "合格" + (f"（発表 {gate['odds_time']}）" if gate.get("odds_time") else "") if gate.get("pass")
            else "・".join(gate.get("reasons") or ["不合格"]))
    fetched = inp.get("card_fetched_at")
    post = post_datetime(race or {})
    if fetched and post:
        try:
            f = datetime.fromisoformat(fetched)
            hours = (post - f).total_seconds() / 3600
            add(hours <= 30, "出馬表の新しさ", f"取得 {f:%m/%d %H:%M}（発走の{hours:.1f}時間前）")
        except ValueError:
            add(None, "出馬表の新しさ", "取得時刻が読めない")
    else:
        add(None, "出馬表の新しさ", "取得時刻の記録が無い")
    return out


def check_summary(items: list[dict]) -> tuple[int, int, int]:
    """（合格・不合格・判定なし）の数。"""
    return (sum(i["ok"] is True for i in items), sum(i["ok"] is False for i in items), sum(i["ok"] is None for i in items))


def bias_check(p: dict, stakes: dict) -> list[tuple[str, str]]:
    """（ラベル, 文）のリスト。ラベルは 推論 か 注意。"""
    if not stakes:
        return []
    total = sum(stakes.values()) or 1
    share: dict[int, int] = {}
    for c, v in stakes.items():
        for n in combo_horses(c):
            share[n] = share.get(n, 0) + v
    out = []
    in_all = sorted(n for n in share if all(n in combo_horses(c) for c in stakes))
    if in_all and len(stakes) > 1:
        out.append(("注意", "全部の組に " + "・".join(f"{n}番" for n in in_all) + " が入っている。"
                    + ("この馬" if len(in_all) == 1 else "どれか") + "が4着以下だと全部はずれ"))
    top_c, top_v = max(stakes.items(), key=lambda x: x[1])
    if len(stakes) > 1 and top_v / total >= 0.5:
        out.append(("注意", f"1つの組（{top_c}）に金額の{top_v / total * 100:.0f}%"))
    marks = dict((mk, n) for mk, n in marks_of(p))
    if marks.get("◎") is not None and marks["◎"] not in share:
        out.append(("注意", f"◎（{marks['◎']}番）が買い目に入っていない（期待値で選ぶと起きることがある）"))
    heavy = sorted(share, key=lambda n: -share[n])[:3]
    out.append(("推論", "金額が多くかかっている馬：" + "、".join(f"{n}番 {share[n] / total * 100:.0f}%" for n in heavy)
                + f"（{len(share)}頭に広がっている）"))
    return out


def change_conditions(p: dict, tp: str, race: dict | None = None) -> list[tuple[str, str]]:
    hp = horse_probs(p)
    order = sorted(hp, key=lambda n: (-hp[n], n))
    out = []
    if len(order) >= 4:
        gap = hp[order[2]] - hp[order[3]]
        out.append(("推論", f"★ {order[2]}番と4番手 {order[3]}番の差は{gap * 100:.1f}ポイント。"
                    + ("小さいので、オッズや取消で入れ替わりやすい" if gap < CLOSE_GAP else "ある程度はなれている")))
    if len(order) >= 2:
        gap = hp[order[0]] - hp[order[1]]
        if gap < CLOSE_GAP:
            out.append(("推論", f"◎ {order[0]}番と○ {order[1]}番はほぼ同じ（差 {gap * 100:.1f}ポイント）"))
    inp = p.get("inputs") or {}
    if tp != "15m":
        out.append(("推論", "15分前に新しいオッズが入ると、配分（買い目と金額）が変わることがある"))
    if inp and not inp.get("win_odds_ok"):
        out.append(("推論", "単勝オッズがまだ入っていない。入ると「学習＋単勝オッズ」と統合の予想が動く"))
    out.append(("推論", "取消・除外が出たら、その馬を抜いて次の時点で作り直す"))
    if p.get("confidence") in ("C", "D", "Z"):
        out.append(("注意", f"自信度 {p.get('confidence')}：上位の馬の差が小さく、もともと割れやすいレース"))
    return out


def horse_comment(h: dict) -> str:
    """ルールの8項目（10段階）から短い一言。評価できた項目が少なければそう書く。"""
    g = {k: v for k, v in (h.get("groups") or {}).items() if v is not None}
    if not g:
        return ""
    good = [k for k, v in sorted(g.items(), key=lambda x: -x[1]) if v >= 7.5][:2]
    bad = [k for k, v in sorted(g.items(), key=lambda x: x[1]) if v <= 3.0][:1]
    bits = [f"{GROUP_LABEL.get(k, k)}◎" for k in good] + [f"{GROUP_LABEL.get(k, k)}△" for k in bad]
    text = " ／ ".join(bits) or "目立つ項目なし"
    if (h.get("coverage") or 1) < 0.5:
        text += "（データ少なめ）"
    return text


def card_flags(p: dict | None, stakes: dict, race: dict, has_result: bool, now: datetime | None = None,
               names: dict | None = None) -> list[tuple[str, str]]:
    """（cls, 文）。cls は warn（気をつける）か ''（ふつう）。"""
    now = now or now_jst()
    out = []
    post = post_datetime(race)
    if post and now >= post and not has_result:
        out.append(("", "発走済み"))
    if not p:
        return out
    inp = p.get("inputs") or {}
    if inp.get("scratched_known"):
        out.append(("warn", "取消あり"))
    if stakes:
        items = [i for i in pre_publish_check(p, stakes, race, names) if i["label"] != "三連複オッズの確認"]
        if check_summary(items)[1]:
            out.append(("warn", "チェック×"))
    gate = p.get("odds_gate")
    if gate is not None and not gate.get("pass"):
        reasons = gate.get("reasons") or []
        out.append(("warn", "古いオッズ" if any("古い" in r for r in reasons) else "オッズ確認×"))
    return out
