"""これからのレースの情報を、時刻つきで集める（5分ごとに tick を1回呼ぶ想定）。

1回の tick でやること：
  1. 30分に1回、JRAの出馬表の一覧から開催とレース（発走時刻・出馬表とオッズのリンク）を更新する
  2. 各レースについて、予想の時点の窓に入っていて、まだ取っていなければ取る
       24h ：締切は発走の24時間前。その15分前から締切まで   出馬表・単勝・三連複（未発売なら「オッズ未発表」と記録）
       1h  ：締切は発走の60分前。その15分前から締切まで     出馬表（馬体重入り）・単勝・三連複
       15m ：締切は発走の15分前。その15分前から締切まで     出馬表（取消の確認）・単勝・三連複
       result：発走の30分後〜6時間後     レース結果（払戻）
  3. 取ったページはそのまま保存し、snapshots.jsonl に「いつ・何を・どこに・ハッシュ」を1行足す

ページの中身の読取（オッズの表など）は保存とは別に行う。読み方を直しても、保存したページから読み直せる。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lxml import html as lxml_html

from . import jra

JST = timezone(timedelta(hours=9))
# 時点ごとの締切（発走の何分前か）。その時点の予想には、締切までに取ったページだけを使う（あいすとの取り決め）
DEADLINE_MIN = {"24h": 24 * 60, "1h": 60, "15m": 15}
CAPTURE_LEAD_MIN = 15  # 締切の15分前から締切までの間に取る（5分ごとの実行で3回の機会）
WINDOWS = {  # 発走からの相対（分）：[開始, 終了)
    **{tp: (-d - CAPTURE_LEAD_MIN, -d) for tp, d in DEADLINE_MIN.items()},
    "result": (30, 6 * 60),
}


def deadline(race_date: str, post_time: str, tp: str) -> datetime:
    post = datetime.fromisoformat(f"{race_date}T{post_time}:00+09:00")
    return post - timedelta(minutes=DEADLINE_MIN[tp])


REFRESH_MINUTES = 30
ARCHIVE_FILE = "races_archive.jsonl"   # 予定表から外したレースの記録（追記だけ）
KEEP_FIELDS = ("post_time", "field_size", "race_name", "surface", "distance_m")   # 一覧の再取得で欠けても前の値を残す欄
ARCHIVE_FIELDS = ("race_date", "venue", "race_number", "race_name", "post_time", "field_size", "surface", "distance_m")
CANCEL_FILE = "cancellations.jsonl"   # JRAが発表したレースの取りやめ（追記だけ）
NOTICE_MINUTES = 30   # 取りやめのお知らせ（ニュースの月別一覧）を見に行く間隔
ODDS_TABS = {"win": ("単勝",), "trio": ("3連複", "３連複")}


def _ends_without_newline(path: Path) -> bool:
    """ファイルの最後が改行で終わっていないか（前回の追記が途中で止まったか）。OS の読取で最後の1バイトだけ見る。"""
    try:
        size = os.stat(path).st_size
    except FileNotFoundError:
        return False
    if size == 0:
        return False
    fd = os.open(path, os.O_RDONLY)
    try:
        os.lseek(fd, -1, os.SEEK_END)
        return os.read(fd, 1) != b"\n"
    finally:
        os.close(fd)


def append_lines(path: Path, lines: list[str]):
    """1行ずつの記録ファイル（JSON Lines）に追記する。前回の追記が途中で止まって最後が改行で終わっていなければ、
    先に改行を足してその行を閉じる（新しい行がくっつかないように。途中の行は読む側で飛ばす）。"""
    text = ("\n" if _ends_without_newline(path) else "") + "".join(x + "\n" for x in lines)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except (AttributeError, OSError, ValueError):
            pass


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    """JSON Lines を読む。（読めた行, 読めなかった行の数）。途中で止まった行などは飛ばす。"""
    rows, bad = [], 0
    if not path.exists():
        return rows, bad
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            x = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(x, dict):
            rows.append(x)
        else:
            bad += 1
    return rows, bad


def result_issue(page: str, field_size) -> str | None:
    """結果のページがまだそろっていない理由（精算と同じ判定。払戻の欠けも含む）。そろっていれば None。"""
    from ..settle import result_problem, winning_combos
    try:
        parsed = jra.parse_result(page)
    except Exception as exc:
        return f"結果のページを読めない：{exc!r}"
    if parsed.get("page_incomplete"):
        return "結果のページが最後まで届いていない"
    rows = parsed["rows"]
    if not rows:
        return "結果の行が無い"
    finished = [(x["horse_number"], x["finish_position"]) for x in rows if x["status"] == "finished"]
    scratched = {x["horse_number"] for x in rows if x["status"] in ("withdrawn", "excluded")}
    payouts = {x["combo"]: x["payout_yen"] for x in parsed.get("trio_payouts", [])}
    problem = result_problem(finished, len(finished), scratched, payouts, len(rows), field_size,
                             [x["horse_number"] for x in rows])
    if problem:
        return problem
    winners = winning_combos(finished)
    if winners and not winners <= set(payouts):
        return "三連複の払戻がまだそろっていない"
    return None


def load_cancellations(path: Path) -> list[dict]:
    """取りやめの記録（cancellations.jsonl）。形のおかしい行は使わない。"""
    out = []
    for x in read_jsonl(path)[0]:
        if (isinstance(x.get("race_date"), str) and isinstance(x.get("venue"), str)
                and isinstance(x.get("from_race"), int) and not isinstance(x.get("from_race"), bool)
                and (x.get("to_race") is None or (isinstance(x["to_race"], int) and not isinstance(x["to_race"], bool)))):
            out.append(x)
    return out


def cancel_notice(rk: str, cancels: list[dict]) -> dict | None:
    """このレースを取りやめると発表したお知らせ（無ければ None）。rk は「日付|場|番号」。"""
    parts = rk.split("|")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    d, venue, num = parts[0], parts[1], int(parts[2])
    for c in cancels:
        if c["race_date"] == d and c["venue"] == venue and c["from_race"] <= num and (c["to_race"] is None or num <= c["to_race"]):
            return c
    return None


def cancelled_result(rk: str, cancels: list[dict], parsed: dict | None) -> dict | None:
    """取りやめとして全額返還にしてよいなら、そのお知らせ。条件は2つとも：
    1. JRAのお知らせがこのレースの取りやめを発表している
    2. 結果のページが無いか、最後まで届いたページに結果の行が1つも無い（行があれば、本当の結果を使う）"""
    notice = cancel_notice(rk, cancels)
    if not notice:
        return None
    if parsed is not None and (parsed.get("page_incomplete") or parsed.get("rows")):
        return None
    return notice


def archived_keys(path: Path) -> set[str]:
    return {x["race_key"] for x in read_jsonl(path)[0] if isinstance(x.get("race_key"), str)}


def _now() -> datetime:
    return datetime.now(JST)


def _tab_links(page: str, words) -> list[tuple[str, str]]:
    """文字に words を含むリンク（オッズの券種タブなど）。"""
    doc = lxml_html.fromstring(page)
    out = []
    for a in doc.xpath("//a"):
        if any(w in re.sub(r"\s+", "", a.text_content()) for w in words):
            out += jra.links(lxml_html.tostring(a, encoding="unicode"))
    return out


class Collector:
    def __init__(self, root: Path, client: jra.Client | None = None, now=_now):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client or jra.Client()
        self.now = now
        self.state_path = self.root / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"races": {}, "taken": {}}

    # ---- 保存 ----
    def _save(self, race_key: str | None, kind: str, tp: str | None, path: str, cname: str, page: str) -> dict:
        ts = self.now()
        sub = Path(*race_key.split("|")) if race_key else Path("_index")
        d = self.root / "snapshots" / sub
        d.mkdir(parents=True, exist_ok=True)
        body = page.encode("utf-8")
        name = f"{ts.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{tp or 'x'}-{kind}.html"
        (d / name).write_bytes(body)
        rec = {"race_key": race_key, "kind": kind, "time_point": tp, "fetched_at": ts.isoformat(timespec="seconds"),
               "path": str((d / name).relative_to(self.root)), "sha256": hashlib.sha256(body).hexdigest(),
               "source": jra.BASE + path, "cname": cname}
        append_lines(self.root / "snapshots.jsonl", [json.dumps(rec, ensure_ascii=False)])
        return rec

    def _write_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1))
        tmp.replace(self.state_path)

    # ---- 取りやめのお知らせ ----
    def check_notices(self, log=print):
        """結果を待っているレースの月のニュース一覧を見て、取りやめのお知らせを cancellations.jsonl に足す。
        失敗しても収集は止めない（次の回にもう一度）。"""
        last = self.state.get("notices_at")
        now = self.now()
        if last and now - datetime.fromisoformat(last) < timedelta(minutes=NOTICE_MINUTES):
            return
        # 結果がまだのレース（予定表に残っている過去7日〜明日。Macが止まっていた日の分も含む）の月を見る
        tomorrow = (now + timedelta(days=1)).date().isoformat()
        days = {r["race_date"] for rk, r in self.state["races"].items()
                if isinstance(r.get("race_date"), str) and r["race_date"] <= tomorrow and f"{rk}|result" not in self.state["taken"]}
        # 前日に「明日【1日】の…」と出ることがあるので、前の日の月も見る
        months = sorted({x for d in days for x in (d[:7], (datetime.fromisoformat(d) - timedelta(days=1)).strftime("%Y-%m"))})
        if not months:
            return
        known = {(c["race_date"], c["venue"], c["from_race"], c["to_race"]) for c in load_cancellations(self.root / CANCEL_FILE)}
        new = []
        try:
            for ym in months:
                path = f"/news/{ym.replace('-', '')}/"
                page = self.client.get_url(jra.BASE + path)
                rec = self._save(None, "notice", None, path, "", page)
                for c in jra.parse_cancellations(page, int(ym[:4]), int(ym[5:7])):
                    if c["race_date"] is None:   # 日付の無い形：回次・日次が同じ予定表のレースから日付を決める（無ければ次の回）
                        days = {r["race_date"] for r in self.state["races"].values()
                                if r.get("venue") == c["venue"] and r.get("kai") == c["kai"] and r.get("nichi") == c["nichi"]}
                        if len(days) != 1:
                            continue
                        c = {**c, "race_date": days.pop()}
                    key = (c["race_date"], c["venue"], c["from_race"], c["to_race"])
                    if key in known:
                        continue
                    known.add(key)
                    new.append(json.dumps({**c, "found_at": now.isoformat(timespec="seconds"), "source": jra.BASE + path,
                                           "sha256": rec["sha256"]}, ensure_ascii=False))
            if new:
                append_lines(self.root / CANCEL_FILE, new)
        except Exception as exc:
            log(f"取りやめのお知らせを見られなかった（次の回にもう一度）：{exc!r}")
            return
        self.state["notices_at"] = now.isoformat(timespec="seconds")

    # ---- 開催とレースの更新 ----
    def refresh(self, force: bool = False):
        last = self.state.get("refreshed_at")
        if not force and last and self.now() - datetime.fromisoformat(last) < timedelta(minutes=REFRESH_MINUTES):
            return
        index = self.client.get(*jra.ENTRY_INDEX)
        for path, cname in jra.links(index):
            if not cname.startswith("pw01drl"):
                continue
            page = self.client.get(path, cname)
            meeting = jra.parse_race_list(page)
            if not meeting.get("race_date"):
                continue
            for r in meeting["races"]:
                rk = f"{meeting['race_date']}|{meeting['venue']}|{r['race_number']}"
                prev = self.state["races"].get(rk, {})
                # 一覧の一部の欄だけ読めなかったとき（None）は、前に確かめた値（頭数・発走時刻など）を消さない
                r = {**r, **{k: prev[k] for k in KEEP_FIELDS if r.get(k) is None and prev.get(k) is not None}}
                self.state["races"][rk] = {**r, "race_date": meeting["race_date"], "venue": meeting["venue"],
                                           "kai": meeting.get("kai"), "nichi": meeting.get("nichi"), "list_cname": cname,
                                           "known_at": prev.get("known_at", self.now().isoformat(timespec="seconds"))}
        self.state["refreshed_at"] = self.now().isoformat(timespec="seconds")
        self._write_state()

    # ---- 取る ----
    def due(self) -> list[tuple[str, str]]:
        out = []
        now = self.now()
        for rk, r in self.state["races"].items():
            if not r.get("post_time"):
                continue
            post = datetime.fromisoformat(f"{r['race_date']}T{r['post_time']}:00+09:00")
            for tp, (a, b) in WINDOWS.items():
                if f"{rk}|{tp}" in self.state["taken"]:
                    continue
                if post + timedelta(minutes=a) <= now < post + timedelta(minutes=b):
                    out.append((rk, tp))
        return sorted(out, key=lambda x: (self.state["races"][x[0]]["post_time"], x[0]))

    def capture(self, rk: str, tp: str) -> list[dict]:
        r = self.state["races"][rk]
        saved = []
        # 一覧を取り直す（オッズや結果のリンクは発売・確定後に出てくる）
        page = self.client.get("/JRADB/accessD.html", r["list_cname"])
        fresh = {x["race_number"]: x for x in jra.parse_race_list(page)["races"]}.get(r["race_number"], {})
        r.update({k: v for k, v in fresh.items() if v})
        if tp == "result":
            for path, cname in (r.get("result_links") or [])[:1]:
                page = self.client.get(path, cname)
                saved.append(self._save(rk, "result", tp, path, cname, page))
                issue = result_issue(page, r.get("field_size"))
                if issue and cancelled_result(rk, load_cancellations(self.root / CANCEL_FILE), jra.parse_result(page)):
                    issue = None   # JRAが取りやめを発表し、結果の行も無い：取り直しをやめる（返還は精算の側で行う）
                if issue:
                    # 途中までの結果（着順の空欄・払戻の欠けなど）：保存はするが「取得済み」にせず、窓の間は次の回に取り直す
                    saved[-1]["incomplete"] = issue
                    return saved
        else:
            if r.get("card_cname"):  # 出馬表は毎時点取る（取消を、その時点の締切までの情報で反映するため）
                saved.append(self._save(rk, "card", tp, "/JRADB/accessD.html", r["card_cname"],
                                        self.client.get("/JRADB/accessD.html", r["card_cname"])))
            odds_links = r.get("odds_links") or []
            if not odds_links:
                saved.append({"race_key": rk, "kind": "odds_unavailable", "time_point": tp,
                              "fetched_at": self.now().isoformat(timespec="seconds"), "odds_status": r.get("odds_status")})
                append_lines(self.root / "snapshots.jsonl", [json.dumps(saved[-1], ensure_ascii=False)])
            else:
                path, cname = odds_links[0]
                first = self.client.get(path, cname)
                saved.append(self._save(rk, "odds_first", tp, path, cname, first))
                for kind, words in ODDS_TABS.items():
                    tabs = [l for l in _tab_links(first, words) if l[1] != cname]
                    if tabs:
                        p2, c2 = tabs[0]
                        saved.append(self._save(rk, f"odds_{kind}", tp, p2, c2, self.client.get(p2, c2)))
        if tp == "result" and not saved:
            return saved  # 結果のリンクがまだ無い。次の tick でもう一度
        self.state["taken"][f"{rk}|{tp}"] = self.now().isoformat(timespec="seconds")
        self._write_state()
        return saved

    def missed(self) -> list[str]:
        """窓が過ぎたのに取れていない（Macが寝ていた・失敗したなど）。結果は6時間の窓が過ぎたら。"""
        out = []
        now = self.now()
        cancels = load_cancellations(self.root / CANCEL_FILE)
        for rk, r in self.state["races"].items():
            if not r.get("post_time") or cancel_notice(rk, cancels):   # 取りやめのレースは「取れなかった」にしない
                continue
            post = datetime.fromisoformat(f"{r['race_date']}T{r['post_time']}:00+09:00")
            known = datetime.fromisoformat(r.get("known_at", self.state.get("refreshed_at") or now.isoformat()))
            for tp, (a, b) in WINDOWS.items():
                key = f"{rk}|{tp}"
                if key not in self.state["taken"] and now >= post + timedelta(minutes=b) and known <= post + timedelta(minutes=a):
                    out.append(key)
        return out

    def tick(self, log=print, notify=None) -> dict:
        self.refresh()
        done, errors = [], []
        for rk, tp in self.due():
            try:
                done += self.capture(rk, tp)
            except Exception as exc:  # 1レースの失敗で全体を止めない
                errors.append(f"{rk} {tp}: {exc!r}")
                log(errors[-1])
        self.check_notices(log)
        missed = self.missed()
        notified = self.state.setdefault("missed_notified", [])
        new_missed = [k for k in missed if k not in notified]
        if (new_missed or errors) and notify:
            notify(f"取れなかった {len(new_missed)}件・失敗 {len(errors)}件", "; ".join((new_missed + errors)[:3]))
        self.state["missed_notified"] += new_missed
        health = {"checked_at": self.now().isoformat(timespec="seconds"), "missed": missed, "errors": errors,
                  "races_known": len(self.state["races"]), "taken": len(self.state["taken"])}
        (self.root / "health.json").write_text(json.dumps(health, ensure_ascii=False, indent=1))
        # 終わった開催は state から外す（7日より前）。外す前に、レースの情報（頭数・発走時刻など）を
        # races_archive.jsonl に1行ずつ追記して残す（確認アプリが、過去のレースの結果がそろっているかを確かめるのに使う）
        cutoff = (self.now() - timedelta(days=7)).date().isoformat()
        old = [k for k, v in self.state["races"].items() if v["race_date"] < cutoff]
        if old:
            try:
                append_lines(self.root / ARCHIVE_FILE, [
                    json.dumps({"race_key": rk, **{k: self.state["races"][rk].get(k) for k in ARCHIVE_FIELDS},
                                "archived_at": self.now().isoformat(timespec="seconds")}, ensure_ascii=False) for rk in old])
            except OSError as exc:   # 書けなければ予定表に残し、次の回にもう一度
                log(f"過去のレースの記録を書けなかった（予定表に残す）：{exc!r}")
            saved = archived_keys(self.root / ARCHIVE_FILE)
            for rk in old:
                if rk in saved:   # 記録を読み戻せたレースだけ予定表から外す
                    self.state["races"].pop(rk)
        self._write_state()
        return {"requests": self.client.count, "saved": len(done), "races_known": len(self.state["races"]),
                "missed": len(missed), "errors": len(errors)}
