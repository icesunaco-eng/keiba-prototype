"""正規の表（races / runners / payouts）を作る。

土台は2年分の統合版：
  - 着順・タイム・状態：qa_zip の data/runner_results.jsonl（全95,472行）
  - レースの上り：qa_zip の data/race_flow.jsonl
  - 馬ID候補：integrated_dir/combined/runner_candidates.jsonl
出走表の列（枠・性齢・斤量・騎手・調教師・馬体重・確定オッズ）とレース条件（芝ダ・距離・馬場…）は、
統合版では削られているので、元の資料から race_key×馬番 で埋める：
  1. HEROINES の netkeiba 結果ページ（raw/result.html。払戻も入っている）
  2. みゅうZIPの netkeiba 結果表（raw/netkeiba/**/result_table.dom.html と capture_metadata.json）
  3. のあ v3（JRA成績PDFから読んだ表）
騎手・調教師は netkeiba のIDで揃える（のあ分は名前→IDの対応を重なる期間から作る）。
同じ値が複数の資料にあるときは食い違いを conflicts.csv に残す。
三連複の払戻は 1 と JRA成績PDF から取る。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from ..config import Paths
from ..schema import PAYOUTS_COLUMNS, RACES_COLUMNS, RUNNERS_COLUMNS
from . import jra_pdf, netkeiba

STATUS_MAP = {"finished": "finished", "did_not_finish": "did_not_finish",
              "withdrawn": "withdrawn", "excluded": "excluded"}
_RK = re.compile(r'"runner_key"\s*:\s*"([^"]+)"')
_CID = re.compile(r'"candidate_id"\s*:\s*"(\d+)"')


def race_key(date: str, venue: str, number) -> str:
    return f"{date}|{venue}|{int(number)}"


def _norm_name(s) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lstrip("▲△☆★◇")


# ---------- 土台（統合版） ----------

def load_base(paths: Paths) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not paths.qa_zip or not paths.qa_zip.exists():
        raise FileNotFoundError("qa_zip が見つかりません（~/.heroines/paths.json の keiba.qa_zip）")
    runners, races = [], {}
    with zipfile.ZipFile(paths.qa_zip) as z:
        with z.open("data/runner_results.jsonl") as f:
            for line in f:
                r = json.loads(line)
                ft = r.get("finish_time") or {}
                runners.append({
                    "runner_key": r["runner_key"], "race_key": r["race_key"], "race_date": r["date"],
                    "venue": r["venue"], "race_number": r["race_no"], "horse_number": r["horse_number"],
                    "owner": r.get("owner"), "status": STATUS_MAP.get(r["status"], r["status"]),
                    "counts_as_starter": bool(r.get("counts_as_starter")),
                    "finish_position": r.get("finish_position_candidate"),
                    "dead_heat": bool(r.get("dead_heat_marker_candidate")),
                    "finish_time_s": ft.get("seconds_candidate"),
                })
        with z.open("data/race_flow.jsonl") as f:
            for line in f:
                r = json.loads(line)
                rs = r.get("race_sectionals") or {}
                races[r["race_key"]] = {
                    "race_key": r["race_key"], "owner": r.get("owner"),
                    "race_last3f_s": (rs.get("last3f_seconds_candidate") or {}).get("value"),
                    "race_last4f_s": (rs.get("last4f_seconds_candidate") or {}).get("value"),
                    "race_type": r.get("race_type_candidate"),
                }
    run = pd.DataFrame(runners)
    rac = pd.DataFrame(races.values())
    meta = run.groupby("race_key").agg(race_date=("race_date", "first"), venue=("venue", "first"),
                                       race_number=("race_number", "first")).reset_index()
    rac = meta.merge(rac, on="race_key", how="left")
    return rac, run


def load_horse_ids(paths: Paths) -> tuple[dict, dict]:
    ids, race_ids = {}, {}
    if not paths.integrated_dir:
        return ids, race_ids
    cand = paths.integrated_dir / "combined" / "runner_candidates.jsonl"
    with open(cand, encoding="utf-8") as f:
        for line in f:
            head = line[:4000]
            k, c = _RK.search(head), _CID.search(head)
            if not (k and c):
                k, c = _RK.search(line), _CID.search(line)
            if k and c:
                ids[k.group(1)] = c.group(1)
    with open(paths.integrated_dir / "combined" / "races.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("provider_race_id"):
                race_ids[r["race_key"]] = r["provider_race_id"]
    return ids, race_ids


# ---------- 出走表の資料 ----------

def iter_heroines(paths: Paths):
    for root in paths.heroines_dirs:
        for f in sorted(root.glob("*/*/*R/raw/result.html")):
            date, venue, r = f.parts[-5], f.parts[-4], f.parts[-3]
            page = netkeiba.parse_page(f.read_text(encoding="utf-8", errors="replace"))
            yield race_key(date, venue, int(r[:-1])), "nk_page", page


def iter_myu(paths: Paths):
    for zp in paths.myu_zips:
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                if not name.endswith("result_table.dom.html"):
                    continue
                meta_name = name.replace("result_table.dom.html", "capture_metadata.json")
                try:
                    meta = json.loads(z.read(meta_name))
                except KeyError:
                    continue
                page = netkeiba.parse_page(z.read(name).decode("utf-8", "replace"), meta.get("header_text", ""))
                yield race_key(meta["race_date"], meta["venue"], meta["race_number"]), "nk_dom", page


def _v3_race(r: dict) -> tuple[str, dict]:
    """のあ v3 形式（JRA成績PDFから読んだ1レース）を、共通の形にする。"""
    cls = None
    for word, c in netkeiba.CLASS_WORDS:
        if word in (r.get("header_raw") or ""):
            cls = c
            break
    race = {"surface": r.get("surface"), "distance_m": r.get("distance_m"), "going": r.get("going"),
            "weather": r.get("weather"), "field_size": r.get("field_size"),
            "post_time": r.get("post_time_scheduled"), "race_class": cls,
            "turn": (re.search(r"(右|左|直線)", r.get("surface_detail") or "") or [None, None])[1]}
    rows = []
    for h in r["horses"]:
        diff = unicodedata.normalize("NFKC", h.get("body_weight_diff_raw") or "").replace("−", "-").replace("±", "")
        sex = {"騸": "セ"}.get(h.get("sex"), h.get("sex"))
        rows.append({
            "horse_number": h.get("umaban"), "waku": h.get("waku"), "horse_name": h.get("name"),
            "sex": None if sex and "{" in sex else sex, "age": h.get("age"), "weight_carried": h.get("weight_carried"),
            "jockey_name": _norm_name(h.get("jockey")), "jockey_id": None,
            "trainer_name": _norm_name(h.get("trainer")), "trainer_id": None,
            "body_weight": h.get("body_weight"),
            "body_weight_diff": int(diff) if re.fullmatch(r"[+\-]?\d+", diff or "") else None,
            "final_win_odds": h.get("win_odds"), "final_popularity": None,
            "last3f_s": None, "passage_raw": None, "margin_raw": h.get("margin_raw"),
        })
    date = r.get("race_date") or f"{r['year']}-{r['month']:02d}-{r['day']:02d}"
    return race_key(date, r["place"], r["race_no"]), {"race": race, "rows": rows, "trio_payouts": []}


def iter_noa(paths: Paths):
    if not paths.noa_v3_dir:
        return
    f = paths.noa_v3_dir / "races.jsonl"
    if not f.exists():
        return
    for line in open(f, encoding="utf-8"):
        rk, page = _v3_race(json.loads(line))
        yield rk, "noa_v3", page


def iter_pdf_tables(paths: Paths):
    """JRA成績PDFを tools/jra_pdf/convert.py で読んだもの（PDF1本＝races/<名前>.json）。
    manifest_csv があれば、その race_key だけを使う（PDFには対象外のレースも載っているため）。"""
    for src in paths.pdf_table_sources:
        allow = None
        if src.get("manifest_csv"):
            import csv
            with open(src["manifest_csv"], encoding="utf-8-sig") as f:
                allow = {r["race_key"] for r in csv.DictReader(f)}
        for f in sorted(Path(src["races_dir"]).glob("*.json")):
            for r in json.load(open(f, encoding="utf-8"))["races"]:
                rk, page = _v3_race(r)
                if allow is None or rk in allow:
                    yield rk, "jra_pdf_table", page


# ---------- 払戻（JRA成績PDF） ----------

def _pdf_worker(args):
    path, cache_dir = args
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = Path(cache_dir) / f"{digest}-v{jra_pdf.PARSER_VERSION}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    try:
        cols = jra_pdf.extract_pdf(path)
    except Exception as exc:  # 壊れたPDFは飛ばして記録する
        cols = [{"file": path.name, "error": repr(exc)}]
    cache.write_text(json.dumps(cols, ensure_ascii=False))
    return cols


def load_pdf_payouts(paths: Paths, runners: pd.DataFrame, workers: int = 6) -> list[dict]:
    if not paths.integrated_dir:
        return []
    locate = {}
    with open(paths.integrated_dir / "jra-baseline" / "expected_races.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            s = r["source"]
            locate[(s["url"].rsplit("/", 1)[1], s["page_1based"], s["column_normalized"])] = r["race_key"]
    files = {}
    for d in paths.jra_pdf_dirs:
        for p in d.rglob("*.pdf"):
            files.setdefault(p.name, p)
    needed = {k[0] for k in locate}
    todo = [str(files[n]) for n in sorted(needed & files.keys())]
    cache_dir = paths.data_dir / "cache" / "jra_pdf"
    cache_dir.mkdir(parents=True, exist_ok=True)
    top3 = (runners[runners["finish_position"].isin([1, 2, 3]) & (runners["status"] == "finished")]
            .groupby("race_key")["horse_number"].apply(lambda s: tuple(sorted(int(x) for x in s))))
    out = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for cols in ex.map(_pdf_worker, [(p, str(cache_dir)) for p in todo]):
            for info in cols:
                if "error" in info:
                    continue
                rk = locate.get((info["file"], info["page"], info["side"]))
                if not rk or int(rk.rsplit("|", 1)[1]) not in info["race_numbers"]:
                    continue
                pay = jra_pdf.column_to_payout(info)
                combo = top3.get(rk)
                if pay and combo is not None and len(combo) == 3:
                    out.append({"race_key": rk, "bet_type": "trio", "combo": "-".join(map(str, combo)),
                                "payout_yen": pay["payout_yen"], "source": "jra_pdf", "check": pay["check"]})
    return out


# ---------- まとめ ----------

ATTR_FIELDS = ["waku", "horse_name", "sex", "age", "weight_carried", "body_weight", "body_weight_diff",
               "final_win_odds", "final_popularity", "last3f_s", "passage_raw", "margin_raw"]
COMPARE_FIELDS = ["waku", "sex", "age", "weight_carried", "body_weight", "final_win_odds"]
RACE_FIELDS = ["surface", "distance_m", "turn", "going", "weather", "race_class", "field_size", "post_time"]
SOURCE_ORDER = ["nk_page", "nk_dom", "noa_v3", "jra_pdf_table"]


def build(paths: Paths, log=print, pdf_workers: int = 6) -> dict:
    out_dir = paths.tables_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    log("統合版を読む")
    races, runners = load_base(paths)
    horse_ids, provider_ids = load_horse_ids(paths)
    runners["horse_id"] = runners["runner_key"].map(horse_ids)
    races["provider_race_id"] = races["race_key"].map(provider_ids)

    log("出走表の資料を読む（netkeiba 結果ページ・結果表、のあ v3）")
    by_source: dict[str, dict] = {s: {} for s in SOURCE_ORDER}
    nk_payouts = []
    for it in (iter_heroines(paths), iter_myu(paths), iter_noa(paths), iter_pdf_tables(paths)):
        for rk, src, page in it:
            by_source[src].setdefault(rk, page)
            if src == "nk_page":
                for p in page["trio_payouts"]:
                    nk_payouts.append({"race_key": rk, "bet_type": "trio", "combo": "-".join(map(str, p["combo"])),
                                       "payout_yen": p["payout_yen"], "source": "netkeiba", "check": "page"})

    # 騎手・調教師：名前→netkeiba ID の対応（同じ runner_key で両方あるものから）
    jockey_map, trainer_map = defaultdict(Counter), defaultdict(Counter)
    for rk, noa in by_source["noa_v3"].items():
        nk = by_source["nk_page"].get(rk) or by_source["nk_dom"].get(rk)
        if not nk:
            continue
        nk_rows = {r["horse_number"]: r for r in nk["rows"]}
        for r in noa["rows"]:
            m = nk_rows.get(r["horse_number"])
            if m and m.get("jockey_id"):
                jockey_map[r["jockey_name"]][m["jockey_id"]] += 1
            if m and m.get("trainer_id"):
                trainer_map[r["trainer_name"]][m["trainer_id"]] += 1

    def resolve(name, mapping, prefix):
        c = mapping.get(name)
        if c:
            best, n = c.most_common(1)[0]
            if n >= 2 and n / sum(c.values()) >= 0.9:
                return f"nk:{best}"
        return f"{prefix}:{name}" if name else None

    log("race_key×馬番で結合し、食い違いを記録する")
    attr_rows, race_rows, conflicts = {}, {}, []
    for rk in races["race_key"]:
        pages = [(s, by_source[s][rk]) for s in SOURCE_ORDER if rk in by_source[s]]
        if not pages:
            continue
        race_attr = {"attr_source": "+".join(s for s, _ in pages)}
        for fld in RACE_FIELDS:
            vals = [(s, p["race"].get(fld)) for s, p in pages if p["race"].get(fld) not in (None, "")]
            if vals:
                race_attr[fld] = vals[0][1]
                if fld in ("surface", "distance_m") and len({v for _, v in vals}) > 1:
                    conflicts.append({"key": rk, "field": fld, "values": json.dumps(vals, ensure_ascii=False)})
        race_rows[rk] = race_attr
        per_horse = defaultdict(list)
        for s, p in pages:
            for r in p["rows"]:
                per_horse[r["horse_number"]].append((s, r))
        for hn, items in per_horse.items():
            rec = {"attr_source": "+".join(s for s, _ in items)}
            for fld in ATTR_FIELDS:
                vals = [(s, r.get(fld)) for s, r in items if r.get(fld) not in (None, "")]
                if vals:
                    rec[fld] = vals[0][1]
                    if fld in COMPARE_FIELDS and len({float(v) if isinstance(v, (int, float)) else v for _, v in vals}) > 1:
                        conflicts.append({"key": f"{rk}|{hn}", "field": fld, "values": json.dumps(vals, ensure_ascii=False)})
            nk = next((r for s, r in items if s in ("nk_page", "nk_dom")), None)
            noa = next((r for s, r in items if s in ("noa_v3", "jra_pdf_table")), None)
            rec["jockey_key"] = (f"nk:{nk['jockey_id']}" if nk and nk.get("jockey_id")
                                 else resolve(noa["jockey_name"], jockey_map, "name") if noa else None)
            rec["trainer_key"] = (f"nk:{nk['trainer_id']}" if nk and nk.get("trainer_id")
                                  else resolve(noa["trainer_name"], trainer_map, "name") if noa else None)
            if nk and nk.get("horse_id"):
                rec["nk_horse_id"] = nk["horse_id"]
            attr_rows[f"{rk}|{hn}"] = rec

    ra = pd.DataFrame.from_dict(race_rows, orient="index").rename_axis("race_key").reset_index()
    races = races.merge(ra, on="race_key", how="left")
    at = pd.DataFrame.from_dict(attr_rows, orient="index").rename_axis("runner_key").reset_index()
    runners = runners.merge(at, on="runner_key", how="left")
    # 統合版に馬IDが無い行は netkeiba 表のリンクから補う。食い違いは記録する
    if "nk_horse_id" in runners:
        diff = runners["horse_id"].notna() & runners["nk_horse_id"].notna() & (runners["horse_id"] != runners["nk_horse_id"])
        for _, r in runners[diff].iterrows():
            conflicts.append({"key": r["runner_key"], "field": "horse_id",
                              "values": json.dumps([["integrated", r["horse_id"]], ["netkeiba", r["nk_horse_id"]]])})
        runners["horse_id"] = runners["horse_id"].fillna(runners["nk_horse_id"])
        runners = runners.drop(columns=["nk_horse_id"])

    log("JRA成績PDFから三連複の払戻を読む（初回は時間がかかる。結果はキャッシュする）")
    pdf_pay = load_pdf_payouts(paths, runners, workers=pdf_workers)
    pay = pd.DataFrame(nk_payouts + pdf_pay, columns=PAYOUTS_COLUMNS)
    both = pay.groupby("race_key")["source"].nunique()
    for rk in both[both > 1].index:
        sub = pay[pay["race_key"] == rk]
        if sub.groupby("source")["payout_yen"].apply(lambda s: tuple(sorted(s))).nunique() > 1:
            conflicts.append({"key": rk, "field": "trio_payout",
                              "values": json.dumps(sub[["source", "combo", "payout_yen"]].values.tolist(), ensure_ascii=False)})
    # 2つの資料が両方あるレースで一致を数える（PDF読取の確かめ）
    nk_races = set(pay.loc[pay["source"] == "netkeiba", "race_key"])
    nk_pay = pay[pay["source"] == "netkeiba"].groupby("race_key")["payout_yen"].apply(lambda s: tuple(sorted(s)))
    overlap = [(p["race_key"], p["payout_yen"]) for p in pdf_pay if p["race_key"] in nk_races]
    cross_check = {"races_in_both": len(overlap),
                   "agree": sum(nk_pay[rk] == (y,) for rk, y in overlap)}
    # 払戻は netkeiba を優先し、無いレースだけPDF（票数照合が合ったもの）を使う
    pay = pay[(pay["source"] == "netkeiba") | (~pay["race_key"].isin(nk_races) & (pay["check"] == "votes_ok"))]

    for col in RACES_COLUMNS:
        if col not in races:
            races[col] = None
    for col in RUNNERS_COLUMNS:
        if col not in runners:
            runners[col] = None
    races = races[RACES_COLUMNS + ["race_type"]].sort_values(["race_date", "venue", "race_number"])
    runners = runners[RUNNERS_COLUMNS + ["venue", "race_number", "owner"]].sort_values(["race_date", "race_key", "horse_number"])
    races.to_parquet(out_dir / "races.parquet", index=False)
    runners.to_parquet(out_dir / "runners.parquet", index=False)
    pay.to_parquet(out_dir / "payouts.parquet", index=False)
    pd.DataFrame(conflicts, columns=["key", "field", "values"]).to_csv(out_dir / "conflicts.csv", index=False)

    has_attr = races["attr_source"].notna()
    report = {
        "races": int(len(races)), "runner_rows": int(len(runners)),
        "races_with_attributes": int(has_attr.sum()),
        "races_with_attributes_by_owner": races[has_attr].groupby("owner").size().to_dict(),
        "races_without_attributes_by_owner": races[~has_attr].groupby("owner").size().to_dict(),
        "attr_source_counts": races["attr_source"].value_counts().to_dict(),
        "runner_rows_with_horse_id": int(runners["horse_id"].notna().sum()),
        "distinct_horse_ids": int(runners["horse_id"].nunique()),
        "jockey_key_kinds": runners["jockey_key"].dropna().str.split(":").str[0].value_counts().to_dict(),
        "races_with_trio_payout": int(pay["race_key"].nunique()),
        "trio_payout_sources": pay.groupby("source")["race_key"].nunique().to_dict(),
        "pdf_payout_checks": Counter(p["check"] for p in pdf_pay),
        "pdf_vs_netkeiba_payout": cross_check,
        "conflicts_by_field": Counter(c["field"] for c in conflicts),
        "date_range": [str(races["race_date"].min()), str(races["race_date"].max())],
    }
    (out_dir / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return report
