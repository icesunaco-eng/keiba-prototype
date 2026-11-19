"""ルール方式 rule-v1.0（あいすの正式な初版。~/競馬/あいすの引継ぎ受領/rule-v1.0.yaml の実装）。

仕様からの扱い：
  - 対象は JRA平地の芝・ダート。障害は全頭「対象外（Z）」
  - 仕様では「検証済みIDだけで結合」「結果版の公開時刻が予想時刻より前と確認できたものだけ」だが、
    今の過去データは馬IDが候補で、公開時刻の証拠もない。そこで仕様の「offline_proxy（保存された結果版だけを
    使う研究・再現）」モードとして動かす：前日までの確定レースを使い、候補IDで結合する。発走前検証とは呼ばない
  - 同日の結果は使わない。そのレース自身の結果・確定オッズ・人気は使わない

計算は、全出走を日付順に1回なめて各馬の群の効用 u を作り（as-of）、その後レースごとに10段階にする。
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict, deque
from decimal import ROUND_HALF_EVEN, Decimal

import numpy as np
import pandas as pd

RULE_V1_VERSION = "rule-v1.0"
MODE = "offline_proxy"
GROUPS = ["ability", "recent", "distance", "course", "going", "pace", "draw", "weight_rider"]
WEIGHTS = {g: 1.0 for g in GROUPS}
DECAY = 0.75
WINDOW_DAYS = 365
MIN_COVERAGE = 0.5


# ---------- 10段階にする（仕様 common.quantize） ----------

def _round9(x: float) -> Decimal:
    return Decimal(repr(float(x))).quantize(Decimal("1e-9"), rounding=ROUND_HALF_EVEN)


def quantize(utilities: list) -> tuple[list, bool]:
    """効用の並び → (点数の並び, 全員同値か)。無効（None/NaN）は None。
    有効が2頭未満または半数未満なら全員 None（未採点）。"""
    n_all = len(utilities)
    valid = [(i, _round9(u)) for i, u in enumerate(utilities) if u is not None and not (isinstance(u, float) and not math.isfinite(u))]
    n = len(valid)
    if n < 2 or n * 2 < n_all:
        return [None] * n_all, False
    vals = sorted((v for _, v in valid), reverse=True)  # 順位1が最良
    out = [None] * n_all
    if vals[0] == vals[-1]:
        for i, _ in valid:
            out[i] = 6
        return out, True
    for i, v in valid:
        first = bisect_left([-x for x in vals], -v) + 1          # 同値ブロックの最小順位
        last = first + vals.count(v) - 1                          # 最大順位
        mid = Decimal(first + last) / 2
        p = (Decimal(n) - mid) / Decimal(n - 1)
        out[i] = min(10, 1 + int((p * 10).to_integral_value(rounding="ROUND_FLOOR")))
    return out, False


def total_and_coverage(points: dict, all_tied: dict) -> tuple[float | None, float]:
    """群の点 → (総合点, 被覆)。全員同値の群と未採点の群は数えない。"""
    scored = [g for g in GROUPS if points.get(g) is not None and not all_tied.get(g)]
    cov = sum(WEIGHTS[g] for g in scored) / sum(WEIGHTS.values())
    if not scored or cov < MIN_COVERAGE:
        return None, cov
    return sum(WEIGHTS[g] * points[g] for g in scored) / sum(WEIGHTS[g] for g in scored), cov


def coverage_label(cov: float, total) -> str:
    if total is None or cov < 0.5:
        return "Z"
    if cov >= 0.875:
        return "A"
    if cov >= 0.75:
        return "B"
    if cov >= 0.625:
        return "C"
    return "D"


# ---------- 効用を作る（日付順に1回なめる） ----------

def _decay_mean(values: list) -> float:
    w = [DECAY ** i for i in range(len(values))]
    return float(np.dot(w, values) / sum(w))


def _going_bucket(g) -> str | None:
    if g in ("重", "不良"):
        return "heavy"
    if g in ("良", "稍重"):
        return "light"
    return None


def _flat(surface) -> bool:
    return surface in ("芝", "ダート")


def _first_corner(passage) -> int | None:
    if not isinstance(passage, str) or not passage.strip():
        return None
    head = passage.split("-")[0].strip()
    return int(head) if head.isdigit() else None


class _Ref:
    """基準集団：キーごとに (日付序数, 値, race_key) を時系列で持ち、365日の窓で取り出す。"""

    def __init__(self):
        self.d = defaultdict(deque)

    def add(self, key, day, value, race):
        self.d[key].append((day, value, race))

    def window(self, key, day):
        q = self.d[key]
        while q and q[0][0] < day - WINDOW_DAYS:
            q.popleft()
        return [x for x in q if x[0] < day]


VARIANTS = {
    # 仕様どおり（あいす rule-v1.0）
    "v1.0": dict(window=365, min_runs=2, shrink_k=0.0, cross_surface_w=0.0, ref_fallback=False, prefix="u_"),
    # 案（rule-v1.1）：未採点を減らす。1走から使い、走数が少ないほど全体の真ん中へ寄せる。
    # 期間は730日、芝ダ違いの走は重み0.5、能力の基準集団が足りないときは「全場・同芝ダ・同距離」に広げる
    "v1.1": dict(window=730, min_runs=1, shrink_k=2.0, cross_surface_w=0.5, ref_fallback=True, prefix="u11_"),
    # 比較版（あいすの指示：変更を1つずつ別々に確かめる。どれも v1.0 からの差分は1つだけ）
    "v1.1-window": dict(window=730, min_runs=2, shrink_k=0.0, cross_surface_w=0.0, ref_fallback=False, prefix="u11w_"),
    "v1.1-onerun": dict(window=365, min_runs=1, shrink_k=2.0, cross_surface_w=0.0, ref_fallback=False, prefix="u11o_"),
    "v1.1-surface": dict(window=365, min_runs=2, shrink_k=0.0, cross_surface_w=0.5, ref_fallback=False, prefix="u11s_"),
    "v1.1-ref": dict(window=365, min_runs=2, shrink_k=0.0, cross_surface_w=0.0, ref_fallback=True, prefix="u11r_"),
}
COMPARISON_VARIANTS = ["v1.1", "v1.1-window", "v1.1-onerun", "v1.1-surface", "v1.1-ref"]


def _n(runs) -> float:
    """有効走の数（芝ダ違いは重みの分だけ数える）。"""
    return float(sum(h["sw"] for h in runs))


def _wmean(values, sw, decay=True) -> float:
    w = [(DECAY ** i if decay else 1.0) * x for i, x in enumerate(sw)]
    return float(np.dot(w, values) / sum(w))


def _shrink(raw, n, prior, V) -> float:
    k = V["shrink_k"]
    return raw if k <= 0 else (n * raw + k * prior) / (n + k)


def build_utilities(races: pd.DataFrame, runners: pd.DataFrame, variant: str = "v1.0") -> pd.DataFrame:
    V = VARIANTS[variant]
    cols = ["race_key", "surface", "distance_m", "going", "venue", "race_type"]
    df = runners.drop(columns=[c for c in cols[1:] if c in runners]).merge(races[[c for c in cols if c in races]], on="race_key", how="left")
    df = df[~df["status"].isin(["withdrawn", "excluded"])].copy()
    df["day"] = pd.to_datetime(df["race_date"]).map(pd.Timestamp.toordinal)
    df["starters"] = df.groupby("race_key")["horse_number"].transform("count")  # 不完走を含み、取消・除外を除く
    jump = df["surface"].eq("障害") | df["race_type"].fillna("").str.contains("障害")
    df["is_flat"] = df["surface"].map(_flat) & ~jump

    hist = defaultdict(list)          # horse_id → 過去走（新しい順に並べて使う）
    time_ref = _Ref()                 # (場, 芝ダ, 距離, 馬場区分) → 走破時計
    draw_ref = defaultdict(deque)     # (場, 芝ダ) → (日, 距離, 頭数, {枠: [頭数, 3着内]})
    jockey = defaultdict(deque)       # (騎手, 芝ダ) → (日, 3着内)
    base = defaultdict(deque)         # 芝ダ → (日, 3着内)
    out = []

    for day, g in df.sort_values(["day", "race_key", "horse_number"]).groupby("day", sort=True):
        # ---- 予想（前日までの状態だけ） ----
        for rk, race in g.groupby("race_key", sort=False):
            r0 = race.iloc[0]
            surf, dist, venue, going = r0["surface"], r0["distance_m"], r0["venue"], r0["going"]
            flat = bool(r0["is_flat"])
            # 枠の母集団（レースで共通）
            pop = None
            if flat and pd.notna(dist):
                q = draw_ref[(venue, surf)]
                while q and q[0][0] < day - WINDOW_DAYS:
                    q.popleft()
                tot_n = tot_t = n_races = 0
                per_waku = defaultdict(lambda: [0, 0])
                for d0, dist0, field0, wk in q:
                    if d0 >= day or abs(dist0 - dist) > 200 or abs(field0 - r0["starters"]) > 2:
                        continue
                    n_races += 1
                    for w, (n, t) in wk.items():
                        per_waku[w][0] += n
                        per_waku[w][1] += t
                        tot_n += n
                        tot_t += t
                pop = (tot_n, tot_t, n_races, per_waku)
            # 騎手の基準率
            bq = base[surf]
            while bq and bq[0][0] < day - WINDOW_DAYS:
                bq.popleft()
            base_n = len(bq)
            p0_j = (sum(t for _, t in bq) / base_n) if base_n else None
            weights = race["weight_carried"].astype(float)
            w_ok = weights.notna().sum()
            w_mean = weights.mean() if w_ok >= 2 and w_ok * 2 >= len(race) else None

            for rec in race.itertuples(index=False):
                u = {g_: None for g_ in GROUPS}
                if flat:
                    hid = rec.horse_id if isinstance(rec.horse_id, str) else None
                    cw = V["cross_surface_w"]
                    past = [dict(h, sw=1.0 if h["surface"] == surf else cw) for h in reversed(hist.get(hid, []))
                            if h["day"] >= day - V["window"] and (h["surface"] == surf or cw > 0)] if hid else []
                    # 能力
                    zs = [h for h in past if h["z"] is not None and pd.notna(dist) and h["dist"] is not None and abs(h["dist"] - dist) <= 200][:5]
                    if _n(zs) >= V["min_runs"]:
                        raw = 0.5 * max(h["z"] for h in zs) + 0.5 * _wmean([h["z"] for h in zs[:3]], [h["sw"] for h in zs[:3]], decay=False)
                        u["ability"] = _shrink(raw, _n(zs), 0.0, V)
                    # 近走
                    vf = [h for h in past if h["f"] is not None][:5]
                    if _n(vf) >= V["min_runs"]:
                        raw = 1 - (0.7 * _wmean([h["f"] for h in vf], [h["sw"] for h in vf]) + 0.3 * vf[0]["f"])
                        u["recent"] = _shrink(raw, _n(vf), 0.5, V)
                    # 距離
                    if pd.notna(dist):
                        near = [h for h in past if h["f"] is not None and h["dist"] is not None and abs(h["dist"] - dist) <= 200][:5]
                        if _n(near) >= V["min_runs"]:
                            u["distance"] = _shrink(1 - _wmean([h["f"] for h in near], [h["sw"] for h in near]), _n(near), 0.5, V)
                    # コース
                    same = [h for h in past if h["f"] is not None and h["venue"] == venue][:5]
                    if _n(same) >= V["min_runs"]:
                        u["course"] = _shrink(1 - _wmean([h["f"] for h in same], [h["sw"] for h in same]), _n(same), 0.5, V)
                    # 馬場（前日は未採点。時点の判断は score 側で行う）
                    gb = _going_bucket(going)
                    if gb:
                        sg = [h for h in past if h["f"] is not None and h["gb"] == gb][:5]
                        if _n(sg) >= V["min_runs"]:
                            u["going"] = _shrink(1 - _wmean([h["f"] for h in sg], [h["sw"] for h in sg]), _n(sg), 0.5, V)
                    # 展開脚
                    ds = [h for h in past if h["d3f"] is not None][:3]
                    cs = [h for h in past if h["c"] is not None][:3]
                    if _n(ds) >= V["min_runs"] and _n(cs) >= V["min_runs"]:
                        md = _wmean([h["d3f"] for h in ds], [h["sw"] for h in ds], decay=False)
                        mc = _wmean([h["c"] for h in cs], [h["sw"] for h in cs], decay=False)
                        raw = 0.7 * min(1.0, max(0.0, (1 - md) / 2)) + 0.3 * (1 - mc)
                        u["pace"] = _shrink(raw, min(_n(ds), _n(cs)), 0.5, V)
                    # 枠
                    if pop and pd.notna(rec.waku):
                        tot_n, tot_t, n_races, per_waku = pop
                        wn, wt = per_waku.get(int(rec.waku), [0, 0])
                        if tot_n >= 100 and n_races >= 15 and wn >= 20:
                            p0 = tot_t / tot_n
                            u["draw"] = (wt + 30 * p0) / (wn + 30) - p0
                    # 斤量・騎手
                    if isinstance(rec.jockey_key, str) and pd.notna(rec.weight_carried) and w_mean is not None and base_n >= 200:
                        jq = jockey[(rec.jockey_key, surf)]
                        while jq and jq[0][0] < day - WINDOW_DAYS:
                            jq.popleft()
                        if len(jq) >= 20:
                            q_ = (sum(t for _, t in jq) + 30 * p0_j) / (len(jq) + 30)
                            d = rec.weight_carried - w_mean
                            u["weight_rider"] = 0.75 * q_ + 0.25 * min(1.0, max(0.0, (3 - d) / 6))
                out.append({"runner_key": rec.runner_key, "race_key": rk, "is_flat": flat, **{f"{V['prefix']}{k}": v for k, v in u.items()}})

        # ---- その日の結果を足す ----
        fin = g[g["status"] == "finished"]
        for rk, race in g.groupby("race_key", sort=False):
            r0 = race.iloc[0]
            if not r0["is_flat"]:
                continue
            surf, dist, venue = r0["surface"], r0["distance_m"], r0["venue"]
            gb = _going_bucket(r0["going"])
            rf = fin[fin["race_key"] == rk]
            l3 = rf["last3f_s"].dropna()
            race_l3 = float(l3.mean()) if len(l3) >= 3 and len(l3) * 2 >= r0["starters"] else None
            for rec in race.itertuples(index=False):
                pos = rec.finish_position if rec.status == "finished" and pd.notna(rec.finish_position) else None
                f = (pos - 1) / (rec.starters - 1) if pos is not None and rec.starters > 1 else None
                z = None
                if pos is not None and pd.notna(rec.finish_time_s) and pd.notna(dist) and gb:
                    w = time_ref.window((venue, surf, dist, gb), day)
                    if V["ref_fallback"] and (len(w) < 30 or len({x[2] for x in w}) < 5):
                        w = time_ref.window(("*", surf, dist, gb), day)
                    ref = [v for _, v, _ in w]
                    n_r = len({x[2] for x in w})
                    if len(ref) >= 30 and n_r >= 5:
                        med = float(np.median(ref))
                        mad = float(np.median(np.abs(np.array(ref) - med)))
                        z = max(-4.0, min(4.0, (med - rec.finish_time_s) / max(1.4826 * mad, 0.2)))
                fc = _first_corner(rec.passage_raw)
                hid = rec.horse_id if isinstance(rec.horse_id, str) else None
                if hid:
                    hist[hid].append({
                        "day": day, "race": rk, "surface": surf, "venue": venue, "gb": gb,
                        "dist": int(dist) if pd.notna(dist) else None, "f": f, "z": z,
                        "d3f": (rec.last3f_s - race_l3) if race_l3 is not None and pos is not None and pd.notna(rec.last3f_s) else None,
                        "c": (fc - 1) / (rec.starters - 1) if fc and pos is not None and rec.starters > 1 else None,
                    })
                if isinstance(rec.jockey_key, str):
                    jockey[(rec.jockey_key, surf)].append((day, bool(pos and pos <= 3)))
                base[surf].append((day, bool(pos and pos <= 3)))
            for rec in rf.itertuples(index=False):
                if pd.notna(rec.finish_time_s) and pd.notna(dist) and gb:
                    time_ref.add((venue, surf, dist, gb), day, rec.finish_time_s, rk)
                    time_ref.add(("*", surf, dist, gb), day, rec.finish_time_s, rk)
            if pd.notna(dist):
                wk = defaultdict(lambda: [0, 0])
                for rec in race.itertuples(index=False):
                    if pd.notna(rec.waku) and rec.status in ("finished", "did_not_finish"):
                        wk[int(rec.waku)][0] += 1
                        wk[int(rec.waku)][1] += bool(rec.status == "finished" and rec.finish_position <= 3)
                draw_ref[(venue, surf)].append((day, int(dist), int(r0["starters"]), dict(wk)))
    return pd.DataFrame(out)


def score_race(u: pd.DataFrame, time_point: str, prefix: str = "u_") -> pd.DataFrame:
    """1レース分の効用 → 群の点・総合点・被覆・信頼度（仕様のとおり）。"""
    res = pd.DataFrame(index=u.index)
    tied = {}
    for g in GROUPS:
        vals = [None if pd.isna(x) else float(x) for x in u[f"{prefix}{g}"]]
        if g == "going" and time_point == "24h":
            vals = [None] * len(vals)  # 対象開催日より前の予想では馬場は未採点
        if not u["is_flat"].all():
            vals = [None] * len(vals)
        pts, all_tied = quantize(vals)
        res[g] = pts
        tied[g] = all_tied
    totals, covs = [], []
    for i in res.index:
        t, c = total_and_coverage({g: res.at[i, g] for g in GROUPS}, tied)
        totals.append(t)
        covs.append(c)
    res["rule_total"] = totals
    res["rule_coverage"] = covs
    res["confidence"] = [coverage_label(c, t) for t, c in zip(totals, covs)]
    res.attrs["all_tied"] = tied
    return res


def score_frame(df: pd.DataFrame, time_point: str, prefix: str = "u_") -> pd.DataFrame:
    """特徴表（u_* 列を含む）全体を、レースごとに score_race する。"""
    parts = [score_race(g, time_point, prefix) for _, g in df.groupby("race_key", sort=False)]
    return pd.concat(parts).loc[df.index] if parts else pd.DataFrame(index=df.index)


class RuleV1Model:
    """variant="v1.0" は仕様どおり（総合点なしの馬は一番弱い扱い）。
    "v1.1" は案：未採点を減らした効用を使い、総合点なしの馬はそのレースの総合点の平均として扱う。"""
    name = "rule"

    def __init__(self, time_point: str, variant: str = "v1.0"):
        self.time_point = time_point
        self.variant = variant
        self.prefix = VARIANTS[variant]["prefix"]

    def fit(self, df):
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        total = pd.to_numeric(score_frame(df, self.time_point, self.prefix)["rule_total"], errors="coerce")
        if self.variant != "v1.0":
            total = total.fillna(total.groupby(df["race_key"]).transform("mean"))
        return total.to_numpy(dtype=float)

    def fingerprint(self) -> str:
        return f"{RULE_V1_VERSION}:{self.variant}:{MODE}"


# ---------- 役割A：学習モデルの上位3頭に、別基準（ルール）の8項目の評価を添える ----------

DISAGREE_BOTTOM = 0.3   # 暫定：学習モデルの1位が、ルールの総合点で評価できた馬の下位3割なら「食い違い」の印
GROUP_LABELS = {"ability": "能力", "recent": "近走", "distance": "距離", "course": "コース", "going": "馬場",
                "pace": "展開脚", "draw": "枠", "weight_rider": "斤量・騎手"}


def rule_view(g: pd.DataFrame, p_top3, time_point: str, top: int = 3) -> dict:
    """g：1レースの特徴（u_* 列を含む）、p_top3：学習モデルの3着内確率（g と同じ順）。
    学習モデルがその馬を選んだ理由ではない。別基準での評価を並べるだけ。欠測は「未評価」。"""
    sr = score_race(g, time_point)
    tied = sr.attrs.get("all_tied", {})
    order = np.argsort(-np.asarray(p_top3, dtype=float))[:top]
    horses = []
    for i in order:
        row = sr.iloc[i]
        groups = {GROUP_LABELS[k]: ("未評価" if row[k] is None or pd.isna(row[k]) or tied.get(k) else int(row[k])) for k in GROUPS}
        n_eval = sum(v != "未評価" for v in groups.values())
        horses.append({"n": int(g["horse_number"].iloc[i]), "groups": groups, "evaluated": f"{n_eval}/8",
                       "total": "未評価" if row["rule_total"] is None or pd.isna(row["rule_total"]) else round(float(row["rule_total"]), 2)})
    totals = pd.to_numeric(sr["rule_total"], errors="coerce")
    first = order[0]
    if pd.isna(totals.iloc[first]) or totals.notna().sum() < 3:
        flag = "判定できない（ルールの総合点が未評価）"
    else:
        pct = (totals.rank(ascending=False, method="average").iloc[first] - 1) / (totals.notna().sum() - 1)
        flag = "食い違い" if pct >= 1 - DISAGREE_BOTTOM else "なし"
    return {"basis": f"{RULE_V1_VERSION}（{MODE}）", "note": "学習モデルの選んだ理由ではなく、別基準での評価",
            "top": horses, "disagreement": flag, "disagreement_rule": f"学習モデルの1位がルール総合点の下位{int(DISAGREE_BOTTOM*100)}%（暫定）",
            "used_for_allocation": False}
