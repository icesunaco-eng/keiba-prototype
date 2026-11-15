"""競馬・三連複予想AI 取得層の架空試作（v0.3 第10章の範囲だけ）。
架空データのみ。外部通信・実取得・秘密・実賭けなし。予想モデル・配分アルゴリズム・画面・通知は含めない。
すべての置き場は追記のみ（更新・削除の手段を持たない）。時刻はすべて呼び出し側が渡す（実時計を読まない）。
"""
import copy, hashlib, json
from datetime import datetime, timedelta
from itertools import combinations

from . import config as K

BASE_DATE = datetime(2099, 1, 10)


def H(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def fmt(dt):
    return None if dt is None else dt.strftime("%Y-%m-%d %H:%M:%S")


def all_combos(n=K.FIELD_SIZE):
    return ["%d-%d-%d" % c for c in combinations(range(1, n + 1), 3)]


def horses_of(combo):
    return [int(x) for x in combo.split("-")]


class AppendOnly:
    """追記のみの置き場。行は書込み時に複製し、読出しも複製を返す。"""

    def __init__(self, name):
        self.name = name
        self._rows = []

    def append(self, row):
        row = copy.deepcopy(row)
        row["_seq"] = len(self._rows)
        self._rows.append(row)
        return copy.deepcopy(row)

    def rows(self):
        return [copy.deepcopy(r) for r in self._rows]

    def __len__(self):
        return len(self._rows)


class Race:
    def __init__(self, rid, date, post):
        self.id, self.date, self.post = rid, date, post   # post: 出走表の予定時刻
        self.applied_post = post                           # 最終確定前に反映した変更後の予定
        self.final_at = None                               # 15分前の予想を確定保存した時刻
        self.horses = list(range(1, K.FIELD_SIZE + 1))
        self.field_size = K.FIELD_SIZE

    @property
    def key(self):
        return "%s/%s" % (self.date.strftime("%Y-%m-%d"), self.id)


class System:
    def __init__(self):
        self.races = {}
        for i in range(8):
            rid = "T%02d" % (i + 1)
            self.races[rid] = Race(rid, BASE_DATE, BASE_DATE.replace(hour=10) + timedelta(minutes=30 * i))
        s = lambda n: AppendOnly(n)
        self.content, self.obs, self.body, self.quarantine = s("content"), s("obs"), s("body"), s("quarantine")
        self.defects, self.intermediate, self.prerelease, self.final = s("defects"), s("intermediate"), s("prerelease"), s("final")
        self.predictions, self.eligibility, self.warnings, self.settlements = s("predictions"), s("eligibility"), s("warnings"), s("settlements")
        self.holds, self.notes, self.withdrawals, self.hist_rows, self.hist_preds = s("holds"), s("notes"), s("withdrawals"), s("hist_rows"), s("hist_preds")
        self.halted = False
        self.judge_clock = {"independent": True, "trusted": True}
        self._obs_n = 0
        self._content_index = {}
        self._warn_keys = set()
        self._hold_keys = set()
        self._settle_keys = set()
        self._pred_index = {}
        self.row_access_log = []
        # KD-03：開催日を含むレース識別子（race.key）ごとに Race を保持する。races[rid] は最後に登録した Race。
        self.race_by_key = {r.key: r for r in self.races.values()}
        # KR7-05：予定（出走表の予定時刻）を版つきの追記専用行として保存し、判定の根拠から通し番号で参照する。
        self.schedules = s("schedules")
        for r in self.races.values():
            self._record_schedule(r)
        # KD-05：同じ事象の再配送を見分ける指紋（obs_id を除く事象の中身）。再配送で新しい警告を出さない。
        self._event_fps = set()
        self._redelivery = False
        # KR9-03：確定後の予定再登録で現在の適格性を追記判定するか。設計v0.3§4と旧固定試験（KD8-09・NZ-05c）の期待が衝突するため、
        # 本人・設計レビューの判断まで既定は False（据置き）。True は整合案の診断用で、既定の動作には使わない。
        self.rejudge_schedule_after_final = True   # 🤍20261115-12：本人承認（1794719236.789459）により既定で有効

    # ------------------------------------------------------------ 共通
    def race(self, rid):
        return self.races[rid]

    def add_race(self, rid, date, post, observed_at=None):
        """observed_at：KR9-03。予定の新しい根拠を観測した時刻（分かれば渡す）。予定行と、それによる判定行（trigger_saved）に残す。
        省略時は観測時刻不明（None）として記録し、判定時点は既存の判定時点のまま（論理時点を進めない）。"""
        self._redelivery = False   # KR9-02：予定の登録は配送された事象ではない。直前の別事象の再配送の印を持ち越さない
        key = Race(rid, date, post).key
        r = self.race_by_key.get(key)
        if r is None:
            r = Race(rid, date, post)
            self.race_by_key[r.key] = r
            self.races[rid] = r
            self._record_schedule(r, observed_at)
            return
        # KR8-05：同じ日付キーの予定の再登録。既存の Race（確定保存の時刻・適用中の予定など）を保ち、予定の版だけを上げる。
        # 旧予定の行は schedules に残るので、旧判定の参照も予想本文も変わらない。
        old_post = r.post
        if observed_at is None:
            # 🤍20261115-12 案C（本人委任）：予定の更新は観測時刻を必須とする。観測時刻のない更新は受信順で採用せず隔離し、
            # 予定版にも現在の判定にも入れない。既存の予定版・判定はそのまま残す（初期登録は観測時刻なしでも受け付ける）。
            self.quarantine_row("予定の更新に観測時刻がない（案C）", {"kind": "schedule_update", "race": rid, "race_key": r.key, "post": post})
            self.hold("schedule_update_unobserved", (r.key, fmt(post), len(self.quarantine)), race=r.id, race_key=r.key, post=fmt(post),
                      current_post=fmt(r.post), msg="観測時刻のない予定更新を隔離した。適用中の予定・現在の判定は変えない")
            # KR12-03：拒否する更新では、日付キーを省略したときの現在開催の参照（races[rid]）も変えない
            if r.final_at is not None:
                self.warn("schedule_after_final", (r.key, fmt(post), "unobserved"), race=r.id,
                          change="%s→%s" % (old_post.strftime("%H:%M"), post.strftime("%H:%M")))
            return
        self.races[rid] = r   # 検証を通った更新だけが現在開催の参照を変える（KR12-03）
        rows_k = [x for x in self.schedules.rows() if x["race_key"] == r.key]
        same = [x for x in rows_k if x.get("observed_at") == observed_at and x["post"] == post]
        if same:
            # KR11-02：同じ観測の再登録は新しい根拠ではない。予定版は足さない。
            # KR12-02：ただし前回が途中で止まっていてもよいように、以下の選択・適用・警告・判定を照合して未完の分だけ補う
            self._note_safely({"kind": "schedule_redelivery", "race_key": r.key, "post": fmt(post), "observed_at": fmt(observed_at)})
            prev = [x for x in rows_k if x["_seq"] < same[0]["_seq"]]
            if prev:
                old_post = prev[-1]["post"]
        else:
            self._record_schedule(r, observed_at, post)
        # KR10-02：受信順ではなく根拠の新旧で、適用する予定を選ぶ（適格性と同じ選択規則 _select_schedule）。
        sel_post, status, refs = self._select_schedule(r, None)
        if status == "ok":
            r.post, r.schedule_seq = sel_post, refs[0]["seq"]
        elif status == "conflict":
            # 同じ観測時刻で内容の違う予定：改訂順を証明できないので、適用中の予定は変えずに警告する
            self.warn("schedule_conflict", (r.key, fmt(observed_at), fmt(post)), race=r.id, posts=sorted({fmt(x["post"]) for x in self._schedule_rows(refs)}))
        elif status == "undecided":
            # 観測時刻つきの版の後に、観測時刻不明の異なる版が届いた：優先関係は未決（整合案のみ記録）。適用中の予定は変えずに保留する
            self.hold("schedule_priority_undecided", (r.key, fmt(post), len(self.schedules)), race=r.id, race_key=r.key,
                      posts=sorted({fmt(x["post"]) for x in self._schedule_rows(refs)}),
                      msg="観測時刻不明の予定と観測時刻つきの予定の優先関係が未決のため、現在の判定は未確認とする")
        if r.final_at is not None:
            # 確定後の予定の変更は、確定後の時刻変更と同じく適用中の予定に反映せず、警告する。
            # KR12-01：警告の事象キーに観測時刻を含める（同じ予定値に戻る A→B→A の別観測を別の警告として残す。
            # 同じ観測の再配送は同じキーなので重複しない）
            self.warn("schedule_after_final", (r.key, fmt(post), fmt(observed_at)), race=r.id,
                      change="%s→%s" % (old_post.strftime("%H:%M"), post.strftime("%H:%M")))
            if self.rejudge_schedule_after_final:
                # 承認済み：確定後も本文・旧判定・旧参照と適用中の予定は変えず、現在の適格性だけを追記判定する
                self._sync_schedule_judgements(r, observed_at)
            return
        if r.applied_post == old_post:
            r.applied_post = r.post   # 確定前で、時刻変更をまだ適用していなければ、選んだ予定を適用する
        # KR9-03／KR10-04：確定前の予定更新は、既存予想の現在の適格性を照合し、新しい判定と違う予想だけに追記する
        # （本文・旧判定行・旧参照は不変。同じ更新の再試行では、前回途中で終わった予想だけが補われる）
        self._sync_schedule_judgements(r, observed_at)

    def _sync_schedule_judgements(self, r, observed_at):
        latest = self.latest_eligibility()
        for p in self.predictions.rows():
            if p.get("race_key") == r.key and p["mode"] == "forward":
                cand = self._eligibility_row(p, observed_at, "予定の更新")
                cur = latest.get(p["pred_id"])
                if cur is None or self._judgement_changed(cur, cand):
                    self.eligibility.append(cand)

    @staticmethod
    def _judgement_changed(cur, cand):
        """KR11-03：判定の意味（判定・使った発走・根拠の種類）に加え、食い違い・未決など複数の根拠に基づく判定では
        根拠参照の集合の変化も照合する。同じ予定を確かめるだけの新しい観測（単一の予定根拠で発走も同じ）は判定を足さない（KD10-15）。"""
        if (cur["verdict"], cur["post_used"], cur["basis"]) != (cand["verdict"], cand["post_used"], cand["basis"]):
            return True
        if cand["basis"] == "予定":
            return False
        key = lambda e: sorted((r.get("store"), r.get("seq")) for r in e["basis_refs"])
        return key(cur) != key(cand)

    def _schedule_rows(self, refs):
        rows = self.schedules.rows()
        return [rows[x["seq"]] for x in refs if x.get("store") == "schedules"]

    def _select_schedule(self, r, at):
        """KR10-02／KR10-03：予定版の選択規則（適用中の予定と適格性の共通規則）。戻り値 (post, status, refs)。
        - 判定時点 at までに観測された版だけを候補にする（at=None は既知のすべて）。利用可能な版がなければ status="none"（未来の版へ戻らない）
        - 観測時刻つきの版は、観測時刻が最も新しいものを選ぶ。同じ観測時刻で予定が違えば status="conflict"（改訂順を証明できない）
        - 観測時刻不明の版は、登録した時点から利用できるとみなす（KR9-03 の宣言済み動作）。不明どうしは後の登録を選ぶ
        - 観測時刻つきの版より後に、観測時刻不明の異なる版が登録されたら、優先関係は未決として status="undecided"
          （🤍20261115-12 の案Cでは観測時刻なしの更新は予定版に入らないので、この分岐は生じない。旧版の記録の防御として残す）
          （どちらを優先するかは設計判断。今回は独断で採用しない）"""
        rows = [x for x in self.schedules.rows() if x["race_key"] == r.key]
        def ref(x):
            return {"store": "schedules", "seq": x["_seq"], "kind": "schedule", "race_key": r.key}
        known = [x for x in rows if x.get("observed_at") is not None and (at is None or x["observed_at"] <= at)]
        # 案C（🤍20261115-12）：観測時刻なしの版は初期登録だけ（更新は隔離されて予定版に入らない）。初期登録は、
        # 判定時点までに観測時刻つきの版がないときだけ根拠にする（KR9-03 の宣言動作のうち初期登録の部分を維持）
        unknown = [x for x in rows if x.get("observed_at") is None]
        if known:
            mt = max(x["observed_at"] for x in known)
            top = [x for x in known if x["observed_at"] == mt]
            if len({x["post"] for x in top}) > 1:
                return None, "conflict", [ref(x) for x in top]
            best = top[-1]
            late_unknown = [x for x in unknown if x["_seq"] > max(t["_seq"] for t in top) and x["post"] != best["post"]]
            if late_unknown:
                return None, "undecided", [ref(best)] + [ref(x) for x in late_unknown]
            return best["post"], "ok", [ref(best)]
        if unknown:
            return unknown[-1]["post"], "ok", [ref(unknown[-1])]
        return None, "none", []

    def _record_schedule(self, r, observed_at=None, post=None):
        """KR7-05：同じ日付キーで登録し直しても旧行は残る（版を上げて追記するだけ）。KR9-03：観測時刻（不明なら None）も残す。
        KR10-02：再登録では受けた版を記録するだけで、適用する版は _select_schedule で選ぶ。"""
        version = 1 + sum(1 for x in self.schedules.rows() if x["race_key"] == r.key)
        row = self.schedules.append({"race_key": r.key, "race": r.id, "date": r.date, "post": r.post if post is None else post,
                                     "version": version, "observed_at": observed_at})
        if post is None:
            r.schedule_seq = row["_seq"]

    def _resolve_race(self, ev, kind, raw):
        """KR7-03：入口で日付込みのレース識別子を解決・検査する（共通）。
        明示したキーが未登録・レース番号と不一致なら隔離し、別のレースへ付け替えない。
        キーがないときは、同じレース番号の開催が1つだけなら採用し、複数なら一意に決められないので保留する。"""
        rid, key = ev.get("race"), ev.get("race_key")
        if key is not None:
            r = self.race_by_key.get(key)
            if r is None or r.id != rid:
                self.quarantine_row("日付キーが未登録・レース番号と不一致（%s）" % key, raw)
                return None
            return r
        cands = [r for r in self.race_by_key.values() if r.id == rid]
        if not cands:
            self.quarantine_row("レースが未登録", raw)
            return None
        # 行そのものが開催日を持つ種類（実発走の時刻、時刻変更の変更前予定）は、その日付で一意に決められる。
        # 払戻・馬場など日付を持たない行は、保存時刻だけでは開催を証明できないので、候補が複数なら保留する。
        intrinsic = ev["actual"].date() if kind == "actual_post" else (ev["old"].date() if kind == "post_change" else None)
        if intrinsic is not None:
            cands = [r for r in cands if r.date.date() == intrinsic]
        if len(cands) == 1:
            return cands[0]
        self.hold("race_key_ambiguous", (kind, rid, fmt(ev.get("saved"))), race=rid, row_kind=kind,
                  candidates=sorted(r.key for r in cands), saved=fmt(ev.get("saved")))
        return None

    def _mark_event(self, kind, ev):
        """KD-05：obs_id を除いた事象の中身が前に処理したものと同じなら再配送。新しい取得は保存完了時刻などが変わる。"""
        fp = H([kind, {k: v for k, v in ev.items() if k != "obs_id"}])
        self._redelivery = fp in self._event_fps
        self._event_fps.add(fp)
        return self._redelivery

    def new_obs_id(self):
        self._obs_n += 1
        return "obs-%04d" % self._obs_n

    def warn(self, kind, key, **info):
        k = (kind,) + tuple(key)
        if k in self._warn_keys:
            return
        if self._redelivery:
            # KD-05：再配送では新しい警告を出さない（初回の処理で出ていない警告は、再配送の順序で生じたもの）。記録だけ残す。
            self.notes.append({"kind": "warning_suppressed_redelivery", "warning": kind, "key": list(map(str, key))})
            return
        # KR11-01：表示内容とは別に、安定した事象キー（event_key）を保存行に残す。保存後の応答不明は同じ事象キーの行と照合して
        # 重複を作らず、内容が同じでも事象キーが違う（別の事象）なら新しい警告として残す
        ek = H([str(x) for x in k])
        if any(w.get("event_key") == ek for w in self.warnings.rows() if w.get("kind") == kind):
            self._warn_keys.add(k)
            return
        row = dict(kind=kind, **info)
        row["event_key"] = ek
        self.warnings.append(row)
        self._warn_keys.add(k)   # KR9-01：保存に成功してから「出した」と記録する（保存失敗の後の再試行で警告を回復できる）

    def hold(self, kind, key, **info):
        k = (kind,) + tuple(key)
        if k in self._hold_keys:
            return
        self._hold_keys.add(k)
        self.holds.append(dict(kind=kind, **info))

    def quarantine_row(self, reason, raw, defect=False):
        self.quarantine.append({"reason": reason, "raw": raw})
        if defect:
            self.defects.append({"kind": "time_missing", "reason": reason})

    def put_content(self, kind, value):
        vh = H(value)
        k = (kind, vh)
        if k not in self._content_index:
            row = self.content.append({"kind": kind, "value_hash": vh, "value": value})
            self._content_index[k] = row["_seq"]
        return self._content_index[k]

    def _rows_of(self, kind, purpose):
        allowed = {"settle": {"payout", "scratch"}}
        if purpose in allowed and kind not in allowed[purpose]:
            raise PermissionError("%s は %s を読めない" % (purpose, kind))
        self.row_access_log.append((purpose, kind))
        return [r for r in self.body.rows() if r["kind"] == kind]

    # ------------------------------------------------------------ 時刻の精度と年の補完
    def complete_year(self, raw, race):
        """発表月日時分（MMDDhhmm）に前年・当年・翌年を当て、開催日の前後3日の窓に入る1つだけを採用する。"""
        if raw is None or len(raw) != 8 or not raw.isdigit():
            return None, "真の欠落"
        mo, d, hh, mi = int(raw[0:2]), int(raw[2:4]), int(raw[4:6]), int(raw[6:8])
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 0 <= hh <= 23 and 0 <= mi <= 59):
            return None, "真の欠落"
        lo = race.date - timedelta(days=K.YEAR_WINDOW_DAYS)
        hi = race.date + timedelta(days=K.YEAR_WINDOW_DAYS + 1)
        cands = []
        for y in (race.date.year - 1, race.date.year, race.date.year + 1):
            try:
                dt = datetime(y, mo, d, hh, mi)
            except ValueError:
                continue
            if lo <= dt < hi:
                cands.append(dt)
        if len(cands) != 1:
            return None, "年の補完不能(%d候補)" % len(cands)
        return cands[0], None

    def skew_adjust(self, skew):
        a = abs(skew or 0.0)
        return a if a > K.SKEW_NORMAL_MAX else 0.0

    # ------------------------------------------------------------ オッズの観測
    def ingest_odds(self, ev):
        """ev: race, stage, ended(datetime), announce_raw, values(dict or None), result, skew, obs_id, target, adapter, sale"""
        target = ev["target"]
        race = self.race_by_key.get(target) if target in self.race_by_key and self.race_by_key[target].id == ev["race"] else self.races.get(ev["race"])   # KR7-03
        obs_id = ev["obs_id"]
        redelivery = self._mark_event("odds", ev)
        skew = ev.get("skew") or 0.0
        base = {"kind": "odds", "obs_id": obs_id, "target": target, "race": ev["race"], "stage": ev["stage"],
                "ended": ev["ended"], "skew": skew, "adapter": ev.get("adapter", "fictional")}
        raw = dict(base, announce_raw=ev.get("announce_raw"))
        if self.halted:
            self.obs.append(dict(base, result="quarantined", note="取得停止中"))
            self.quarantine_row("取得停止中（時計ずれ）", raw)
            return
        if abs(skew) > K.SKEW_HALT_OVER:
            self.halted = True
            self.obs.append(dict(base, result="quarantined", note="時計ずれ%.1f秒で停止" % skew))
            self.quarantine_row("時計ずれ超過", raw)
            self.warn("clock_skew_halt", (target, fmt(ev["ended"]), skew), skew=skew, row_time=fmt(ev["ended"]))
            return
        adjust = self.skew_adjust(skew)
        if adjust:
            self.warn("clock_skew", (target, fmt(ev["ended"]), skew), skew=skew, row_time=fmt(ev["ended"]))
        # 安全③: 観測ID・対象キー・段階は必須
        if not obs_id or not target or race is None or target != race.key:
            self.quarantine_row("観測ID/対象キーなし・不一致", raw)
            return
        if ev["stage"] is None:
            self.quarantine_row("段階不明", raw)
            self.obs.append(dict(base, result="quarantined", note="段階不明"))
            return
        result = ev.get("result", "success")
        if result in ("failure", "interrupted"):
            self.obs.append(dict(base, result="failure", note=result, adjust=adjust))
            return
        if result == "unpublished":
            start = (race.date - timedelta(days=1)).replace(hour=K.SALE_START_DAY_BEFORE[0], minute=K.SALE_START_DAY_BEFORE[1])
            if ev["ended"] > start + timedelta(minutes=K.SALE_GRACE_MIN):
                self.obs.append(dict(base, result="failure", note="発売開始予定を過ぎても未発表"))
                self.warn("sale_missing", (race.key,), race=race.id, row_time=fmt(ev["ended"]))
            else:
                self.obs.append(dict(base, result="unpublished", sale=ev.get("sale")))
            return
        stage = ev["stage"]
        if ev.get("adapter") == "accumulated":
            stage_store = "final"      # 蓄積系のオッズは一律に確定系
        elif stage == 1:
            stage_store = "intermediate"
        elif stage == 2:
            stage_store = "prerelease"
        elif stage in (3, 4, 5):
            stage_store = "final"
        elif stage in (9, 0):
            stage_store = "notice"
        else:
            self.quarantine_row("段階が定義外", raw)
            return
        announce = None
        if stage_store == "intermediate":
            announce, why = self.complete_year(ev.get("announce_raw"), race)
            if announce is None:
                self.quarantine_row("発表時刻の" + why, raw, defect=(why == "真の欠落"))
                self.obs.append(dict(base, result="quarantined", note=why))
                return
            if announce > ev["ended"]:
                self.quarantine_row("発表が保存完了より後", raw)
                self.obs.append(dict(base, result="quarantined", note="発表が保存より後"))
                return
        values = ev["values"]
        invalid = [c for c, v in values.items() if v not in ("", "000000", "------", "******") and not _is_num(v)]
        for c in invalid:
            self.defects.append({"kind": "parse", "combo": c, "obs_id": obs_id})
        cid = self.put_content("odds", values)
        row = dict(base, result="success", content_id=cid, announce=announce, adjust=adjust, store=stage_store)
        if redelivery:
            row["redelivery"] = True
        self.obs.append(row)
        if stage_store == "intermediate":
            self.intermediate.append({"obs_id": obs_id})
            self._after_final_odds(race, row, values)
        elif stage_store == "prerelease":
            self.prerelease.append({"obs_id": obs_id, "content_id": cid})
        elif stage_store == "final":
            self.final.append({"obs_id": obs_id, "content_id": cid})
        else:
            self.notes.append({"kind": "stage_notice", "stage": stage, "race": race.id})

    def _after_final_odds(self, race, row, values):
        if race.final_at is None:
            return
        pred = self._final_pred(race)
        if not pred or not pred["refs"]:
            return
        old = self.content.rows()[pred["refs"][0]["content_id"]]["value"]
        changed = [b["combo"] for b in pred["bets"] if old.get(b["combo"]) != values.get(b["combo"])]
        if changed:
            self.warn("odds_after_final", (race.key, row["content_id"]), race=race.id, combos=changed, row_time=fmt(row["ended"]))

    # ------------------------------------------------------------ 時点ビュー（前向きモード）
    def obs_rows(self):
        return self.obs.rows()

    def avail_time(self, o):
        t = o["ended"]
        if o.get("announce") is not None:
            t = max(t, o["announce"] + timedelta(seconds=K.MINUTE_PAD_SEC))
        return t + timedelta(seconds=o.get("adjust") or 0.0)

    def schedule(self, rid, tp):
        r = self.races[rid]
        return r.applied_post - K.TIMEPOINT_OFFSET[tp]

    def decision_view(self, rid, tp):
        """判断用のオッズ。中間オッズの置き場だけを読む。返すのは最後に成功した観測1件（鮮度の両方を満たすときだけ）。"""
        r = self.races[rid]
        T = self.schedule(rid, tp)
        acq_max, pub_max = K.FRESHNESS[tp]
        inter_ids = {x["obs_id"] for x in self.intermediate.rows()}
        allo = [o for o in self.obs_rows() if o.get("target") == r.key and o["kind"] == "odds"]
        cands = [o for o in allo if o["obs_id"] in inter_ids and o["result"] == "success" and o["stage"] == 1
                 and o["ended"] <= T and self.avail_time(o) <= T and T < r.applied_post]
        reasons = []
        latest = max(cands, key=lambda o: (o["ended"], o["_seq"])) if cands else None
        since = latest["ended"] if latest else datetime.min
        fails = [o for o in allo if o["result"] == "failure" and since < o["ended"] <= T]
        halts = [o for o in allo if o["result"] == "quarantined" and since < o["ended"] <= T
                 and ("時計ずれ" in (o.get("note") or "") or "停止中" in (o.get("note") or ""))]
        if latest is None:
            unpub = [o for o in allo if o["result"] == "unpublished" and o["ended"] <= T]
            if unpub:
                reasons.append("オッズ未発売")
            elif not fails and not halts:
                reasons.append("未取得")
        else:
            acq_age = (T - latest["ended"]).total_seconds() + (latest.get("adjust") or 0.0)
            pub_age = (T - latest["announce"]).total_seconds()
            if acq_age > acq_max:
                reasons.append("鮮度切れ・取得")
            if pub_age > pub_max:
                reasons.append("鮮度切れ・発表")
        if fails:
            reasons.append("取得失敗")
        if halts:
            reasons.append("時計ずれ")
        usable = latest is not None and not any(x.startswith("鮮度切れ") for x in reasons)
        out = {"T": T, "usable": usable, "reasons": [] if usable else _uniq(reasons), "obs": latest if usable else None}
        if usable:
            out["values"] = self.content.rows()[latest["content_id"]]["value"]
        return out

    def feature_rows(self, rid, T, kind, race_date=None, race_key=None):
        """KR8-03：体重・レース詳細・出走表は当該開催の日付込みキー（race_key、省略時は最後に登録した開催）で選ぶ。
        race_date は過去結果（result）の日付の絞込みで、当該開催のキーとは別物。"""
        r = self.race_by_key[race_key] if race_key is not None else self.races[rid]
        out = []
        for b in self.body.rows():
            if b["kind"] != kind:
                continue
            if race_date and b.get("race_date") != race_date:
                continue
            if kind == "result" and not (b["race_post"] < T):
                continue
            if kind in ("weight", "race_detail", "entries") and b.get("race_key") != r.key:
                continue
            if b["avail"] <= T:
                out.append(b)
        return out

    # ------------------------------------------------------------ 非オッズ行
    def ingest_row(self, ev):
        self._mark_event("row", ev)
        k = ev["kind"]
        saved = ev["saved"]
        raw = {"kind": k, "race": ev.get("race"), "race_key": ev.get("race_key"), "saved": saved}
        if self.halted:
            # KR7-02：時計ずれの停止をすべての観測入口に共有する。停止中の新しい行は隔離し、予想・適格性・精算に使わない。
            self.quarantine_row("取得停止中（時計ずれ）", raw)
            return
        if k == "result":
            if ev.get("race"):
                r = self._resolve_race(ev, k, raw)
                if r is None:
                    return
                rdate, post, rno = r.date, r.post, int(r.id[1:])
            else:
                rdate = ev["race_date"]
                rno = ev["race_no"]
                post = rdate.replace(hour=10) + timedelta(minutes=30 * (rno - 1))
            row = {"kind": "result", "race_date": rdate.strftime("%Y-%m-%d"), "race_no": rno, "race_post": post,
                   "saved": saved, "avail": saved, "basis": "観測"}
            if saved < post:
                self.quarantine_row("結果が発走前に存在（未来情報）", row)
                if rdate == BASE_DATE:
                    self.warn("result_before_post", (row["race_date"], rno), row_time=fmt(saved))
                return
            self.body.append(row)
        elif k == "race_detail":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            created = ev["created"]
            row = {"kind": "race_detail", "race": r.id, "race_key": r.key, "created": created.strftime("%Y-%m-%d"), "saved": saved, "avail": saved}
            if created.date() > saved.date():
                self.quarantine_row("作成日が保存完了より後", row)
                return
            self.body.append(row)
        elif k == "weight":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            ann = ev["announce"]
            self.body.append({"kind": "weight", "race": r.id, "race_key": r.key, "announce": ann, "saved": saved,
                              "avail": max(saved, ann + timedelta(seconds=K.MINUTE_PAD_SEC))})
        elif k == "entries":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            self.body.append({"kind": "entries", "race": r.id, "race_key": r.key, "field_size": ev["field_size"], "horses": ev["horses"],
                              "saved": saved, "avail": saved})
        elif k == "post_change":
            self._post_change(ev, raw)
        elif k == "actual_post":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            if ev["actual"] > saved:
                # KR7-04：起きる前に保存された実発走は時刻矛盾（未来情報）。隔離して判定に使わない。
                self.quarantine_row("実発走が保存より後（時刻矛盾）", dict(raw, actual=ev["actual"]))
                return
            self.body.append({"kind": "actual_post", "race": r.id, "race_key": r.key, "actual": ev["actual"], "saved": saved, "avail": saved})
            if r.final_at is not None:
                self.warn("actual_post_after_final", (r.key, fmt(ev["actual"])), race=r.id, actual=fmt(ev["actual"]), row_time=fmt(saved))
            self._rejudge(r, saved, "実発走")
        elif k == "track":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            self.body.append({"kind": "track", "race": r.id, "race_key": r.key, "value": ev["value"], "old": ev.get("old"), "saved": saved, "avail": saved})
            if r.final_at is not None:
                self.warn("track_after_final", (r.key, fmt(saved), ev["value"]), race=r.id, change="%s→%s" % (ev.get("old"), ev["value"]), row_time=fmt(saved))
        elif k == "payout":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            # KR7-04／KR8-04：発走より前に保存された払戻は時刻矛盾。発走は、知られている最善の根拠（実発走→延期・変更後の発走→予定）で決める。
            # 元の予定（r.post）だけでなく、延期後の適用発走・実発走より前の保存も隔離する。
            post, basis, _refs = self._post_for_judgement(r)
            hold_why = None
            if post is None:
                cands = self._post_candidates(r)
                if not cands:
                    cands = [saved]   # 発走の根拠がない（予定なし）：時刻矛盾は判定できないので下で保留にする
                    hold_why = ("payout_post_unknown", "発走時刻の根拠がなく、払戻の保存が発走後か確かめられない（精算を保留）")
                if saved < min(cands):
                    self.quarantine_row("払戻が発走より前に保存（時刻矛盾）", dict(raw, post_basis=basis))
                    return
                if hold_why is None and saved < max(cands):
                    hold_why = ("payout_post_conflict", "発走時刻の根拠が食い違い、払戻の保存が発走後か確かめられない（精算を保留）")
            elif saved < post:
                self.quarantine_row("払戻が発走より前に保存（時刻矛盾）", dict(raw, post_used=post, post_basis=basis))
                return
            pred = self._final_pred(r)
            if hold_why is None and pred is not None and saved < pred["at"]:
                # KR8-04：予想の作成より前に保存された払戻で、その予想を黙って精算しない（保留）。
                hold_why = ("payout_before_prediction", "払戻の保存が予想の作成より前（精算を保留）")
            row = {"kind": "payout", "race": r.id, "race_key": r.key,
                   "saved": saved, "refund_flag": ev["refund_flag"], "refund_horses": ev["refund_horses"],
                   "not_established": ev["not_established"], "special": ev["special"], "trio": ev["trio"], "correction": ev.get("correction", False),
                   "avail": saved}
            if ev.get("source_key"):
                # JR13-05：取込み元の安定キー（原本ハッシュ＋開催キー）を保存行に残し、同じキーの行が保存済みなら保存し直さない
                if any(b.get("kind") == "payout" and b.get("source_key") == ev["source_key"] for b in self.body.rows()):
                    self._note_safely({"kind": "payout_source_duplicate", "source_key": ev["source_key"]})
                    return
                row["source_key"] = ev["source_key"]
            self.body.append(row)   # hr_seq の欠落（精算行の hr_seq が null）は今回の範囲外のまま変えない
            if hold_why:
                self.hold(hold_why[0], (r.key, fmt(saved)), race=r.id, race_key=r.key, saved=fmt(saved), msg=hold_why[1])
                return
            self._settle(row)
        elif k == "finish_correction":
            r = self._resolve_race(ev, k, raw)
            if r is None:
                return
            self.body.append({"kind": "finish_correction", "race": r.id, "race_key": r.key, "saved": saved, "avail": saved})
            pays = [p for p in self.body.rows() if p["kind"] == "payout" and p.get("race_key") == r.key and p["correction"]]
            if not pays:
                self.notes.append({"kind": "着順訂正あり・払戻訂正なし", "race": r.id, "row_time": fmt(saved)})
        else:
            raise ValueError("未知の行 " + k)

    # ------------------------------------------------------------ 発走時刻と適格性
    def _post_change(self, ev, raw=None):
        raw = raw or {"kind": "post_change", "race": ev.get("race"), "saved": ev["saved"]}
        r = self._resolve_race(ev, "post_change", raw)
        if r is None:
            return
        if ev["announce"] > ev["saved"]:
            # KR7-04：保存より後に発表された時刻変更は時刻矛盾。隔離して予定に反映しない。
            self.quarantine_row("時刻変更の発表が保存より後（時刻矛盾）", dict(raw, announce=ev["announce"]))
            return
        row = {"kind": "post_change", "race": r.id, "race_key": r.key, "announce": ev["announce"], "old": ev["old"], "new": ev["new"],
               "saved": ev["saved"], "avail": ev["saved"]}
        self.body.append(row)
        change = "%s→%s" % (ev["old"].strftime("%H:%M"), ev["new"].strftime("%H:%M"))
        if r.final_at is not None and ev["saved"] >= r.final_at:
            self.warn("post_change_after_final", (r.key, fmt(ev["announce"]), change), race=r.id, change=change,
                      announce=fmt(ev["announce"]), row_time=fmt(ev["saved"]))
        else:
            post, basis, _refs = self._post_for_judgement(r, ev["saved"])
            if basis == "変更後":
                r.applied_post = post
            elif basis == "食い違い":
                self.warn("post_change_conflict", (r.key, fmt(ev["announce"])), race=r.id, row_time=fmt(ev["saved"]))
        self._rejudge(r, ev["saved"], "発走時刻変更")

    def _post_for_judgement(self, r, at=None):
        """戻り値：(判定用の発走時刻, 根拠の種類, 根拠行の不変参照)。KD-03：開催日つきのレース識別子で行を選ぶ。
        SD-01：選んだ行（食い違いなら関与したすべての行）の置き場と通し番号を返す。"""
        def ref(b):
            return {"store": "body", "seq": b["_seq"], "kind": b["kind"], "race_key": b.get("race_key")}
        # KR7-04：判定の時点 at までに利用可能になった行だけを根拠にする（後から保存された行を先取りしない）
        # KR8-01：行の種類と開催を先に絞ってから利用可能時刻を見る（avail を持たない種類の行で落ちない）
        mine = [b for b in self.body.rows() if b["kind"] in ("actual_post", "post_change") and b.get("race_key") == r.key]
        usable = [b for b in mine if at is None or b["avail"] <= at]
        acts = [b for b in usable if b["kind"] == "actual_post"]
        if acts:
            vals = {a["actual"] for a in acts}
            if len(vals) == 1:
                return acts[-1]["actual"], "実際", [ref(acts[-1])]
            return None, "食い違い", [ref(a) for a in acts]
        tcs = [b for b in usable if b["kind"] == "post_change"]
        if tcs:
            last_ann = max(t["announce"] for t in tcs)
            latest = [t for t in tcs if t["announce"] == last_ann]
            news = {t["new"] for t in latest}
            if len(news) == 1:
                return latest[-1]["new"], "変更後", [ref(latest[-1])]
            return None, "食い違い", [ref(t) for t in latest]
        # KR7-05／KR9-03／KR10-02／KR10-03：予定は共通の選択規則で選ぶ。判定時点までに利用できる版がなければ、
        # 未来に観測される版へ戻らず「予定なし」（未確認）とする。食い違い・優先未決も未確認。
        post, status, refs = self._select_schedule(r, at)
        basis = {"ok": "予定", "conflict": "食い違い", "undecided": "予定未決", "none": "予定なし"}[status]
        return post, basis, refs

    def _post_candidates(self, r):
        """払戻の検査用：食い違いのときに関与する発走時刻の候補（実発走が食い違うなら実発走、そうでなければ最新発表の変更後）。"""
        mine = [b for b in self.body.rows() if b["kind"] in ("actual_post", "post_change") and b.get("race_key") == r.key]
        acts = [b["actual"] for b in mine if b["kind"] == "actual_post"]
        if acts:
            return acts
        if not any(b["kind"] == "post_change" for b in mine):
            # KR10-02：予定どうしの食い違い・優先未決なら、関与した予定版の時刻が候補（予定なしなら空）
            return [x["post"] for x in self._schedule_rows(self._select_schedule(r, None)[2])]
        last = max(b["announce"] for b in mine if b["kind"] == "post_change")
        return [b["new"] for b in mine if b["kind"] == "post_change" and b["announce"] == last]

    def _judge(self, pred, judged_at, evidence):
        self.eligibility.append(self._eligibility_row(pred, judged_at, evidence))

    def _eligibility_row(self, pred, trigger_at, evidence):
        """KR8-02：判定の時点は「その予想の前回の判定時点」と「今回の事象の保存時刻」の遅い方（単調）。
        事象の保存時刻（trigger_saved）・判定時点（judged_at）・処理順（_seq）を分けて残す。
        再配送や保存時刻の古い行が後から届いても、既に知っている行を判定から外さない（巻き戻さない）。
        保存時刻の古い別の事実が後から届いた場合も、判定時点がそれ以後なので根拠に入る（捨てない）。"""
        prev = [e["judged_at"] for e in self.eligibility.rows() if e["pred_id"] == pred["pred_id"]]
        judged_at = max(prev + ([trigger_at] if trigger_at is not None else []) or [pred["at"]])   # KR9-03：観測時刻不明なら論理時点を進めない
        r = self.race_by_key.get(pred["race_key"]) or self.races[pred["race"]]
        post, basis, basis_refs = self._post_for_judgement(r, judged_at)
        saved = pred["at"]
        if post is None:
            verdict = "未確認"
        elif saved <= post - timedelta(seconds=K.ELIGIBILITY_MARGIN_SEC):
            verdict = "適格"
        elif saved >= post:
            verdict = "不適格"
        else:
            verdict = "未確認"
        return {"pred_id": pred["pred_id"], "race_key": r.key, "verdict": verdict, "post_used": post, "basis": basis,
                "basis_refs": basis_refs, "evidence": evidence, "judged_at": judged_at, "trigger_saved": trigger_at}

    def _rejudge(self, r, at, evidence):
        for p in self.predictions.rows():
            if p.get("race_key") == r.key and p["mode"] == "forward":
                self._judge(p, at, evidence)

    # ------------------------------------------------------------ 取消と取り下げ（開催日単位の取得）
    def fetch_day(self, ev):
        self._mark_event("day", ev)
        obs_id = self.new_obs_id()
        scope = ev.get("scope", "day")
        target = "%s/%s" % (ev["date"].strftime("%Y-%m-%d"), "day" if scope == "day" else ev["race"])
        result = ev.get("result", "success")
        reached = ev.get("reached_end", result == "success")
        o = {"kind": "day", "obs_id": obs_id, "target": target, "date": ev["date"], "scope": scope, "ended": ev["ended"],
             "result": result, "complete": ev.get("complete", True), "reached_end": reached,
             "parse_errors": ev.get("parse_errors", 0), "quarantined": ev.get("quarantined", 0), "undecidable": []}
        if self.halted:
            o["halted"] = True    # KD-02：時計ずれによる取得停止を、この取得経路にも共有する
        seen = []
        if result == "success":
            seen = sorted(((s["race"], s["horse"], s["kind"], fmt(s["announce"])) for s in ev["scratches"]))
            o["content_id"] = self.put_content("day", seen)
            for (rid, h, kind, ann) in seen:
                raw_s = {"kind": "scratch", "race": rid, "horse": h, "date": fmt(ev["date"]), "saved": ev["ended"]}
                # 🤍20261115-12：本人承認（1794719236.789459）により、停止中に届いた新しい取消だけは安全側として例外採用する。
                # 停止中のほかの情報（非オッズ行・過去再構成・取消の撤回）は従来どおり隔離・判定不能のまま
                # KD-03／KR7-03：開催日つきの識別子でレースを選ぶ。未登録の開催日は隔離し、同じ番号の別日へ付け替えない
                r = self.race_by_key.get("%s/%s" % (ev["date"].strftime("%Y-%m-%d"), rid))
                if r is None:
                    self.quarantine_row("開催日が未登録の取消", raw_s)
                    continue
                if not self._scratch_active(rid, h, ev["ended"], r.key):
                    annd = datetime.strptime(ann, "%Y-%m-%d %H:%M:%S")
                    self.body.append({"kind": "scratch", "race": rid, "race_key": r.key, "horse": h, "type": kind, "announce": annd, "saved": ev["ended"],
                                      "avail": max(ev["ended"], annd + timedelta(seconds=K.MINUTE_PAD_SEC)), "obs_id": obs_id,
                                      **({"adopted_while_halted": True} if self.halted else {})})
                    self.warn("scratch", (r.key, h), race=rid, horse=h, type=kind, after_final=r.final_at is not None,
                              row_time=fmt(ev["ended"]))
        # 取り下げの判定: 前回見えた取消行が今回見えないとき、完全取得の条件をすべて満たす場合だけ
        for s in self._active_scratches(ev["ended"]):
            r = self.race_by_key[s["race_key"]]
            if (s["race"], s["horse"]) in {(x[0], x[1]) for x in seen}:
                continue
            why = None
            if self.halted:
                why = "取得停止中（時計ずれ）"    # KD-02：停止中の取得（空の一覧を含む）を取消の撤回に使わない
            elif ev["date"] != r.date or scope != "day":
                why = "範囲違い"
            elif result != "success" or not reached:
                why = "途中切断"
            elif not o["complete"]:
                why = "部分取得"
            elif o["parse_errors"]:
                why = "解析不備"
            elif o["quarantined"]:
                why = "隔離あり"
            if why:
                o["undecidable"].append({"race": s["race"], "horse": s["horse"], "reason": why})
            else:
                self.withdrawals.append({"race": s["race"], "horse": s["horse"], "at": ev["ended"], "obs_id": obs_id,
                                         "scratch_seq": s["_seq"]})
        self.obs.append(o)

    def _active_scratches(self, T):
        wd = self.withdrawals.rows()
        out = []
        for s in self.body.rows():
            if s["kind"] != "scratch" or s["avail"] > T:
                continue
            if any(w["scratch_seq"] == s["_seq"] and w["at"] <= T for w in wd):
                continue
            out.append(s)
        return out

    def _scratch_active(self, rid, h, T, race_key=None):
        return any(s["race"] == rid and s["horse"] == h and (race_key is None or s.get("race_key") == race_key)
                   for s in self._active_scratches(T))

    # ------------------------------------------------------------ 予想の確定保存（固定ダミー3点）
    def _final_pred(self, r):
        ps = [p for p in self.predictions.rows() if p.get("race_key") == r.key and p["tp"] == K.FINAL_TIMEPOINT and p["mode"] == "forward"]
        return ps[-1] if ps else None

    def confirm(self, rid, tp, at, decision="default", unknown_result=False):
        self._redelivery = False   # KD-05：確定保存は配送された事象ではない（直前の再配送の印を持ち越さない）
        r = self.races[rid]
        key = (r.key, tp, K.METHOD, K.VERSION)   # KD-03：開催日を含む識別子で一意にする
        if key not in self._pred_index:
            # KR10-01：索引だけを信頼しない。正規キーで保存済みの予想を照合し、結果不明の保存（保存後の例外・索引前の中断）を再作成しない
            stored = [p for p in self.predictions.rows() if (p.get("race_key"), p["tp"], p["method"], p["version"]) == key and p["mode"] == "forward"]
            if stored:
                self._pred_index[key] = stored[-1]["pred_id"]
                self._note_safely({"kind": "confirm_index_recovered", "pred_id": stored[-1]["pred_id"], "race_key": r.key, "tp": tp})
        if key in self._pred_index:           # 保存先を先に確かめ、あれば作らない
            return self._repair_confirm(r, tp, key)   # KR9-01：途中で止まった保存なら整合を確かめて修復する
        if not (self.judge_clock["independent"] and self.judge_clock["trusted"]):
            self.hold("judge_clock_uncertain", key, race=rid, tp=tp, at=fmt(at))
            return {"status": "held"}
        # KR10-03：予想の時点・ビューも、判断時点までに利用できる予定に限る。適用中の予定版が判断時点より後に観測されたもの、
        # または判断時点で利用できる予定がなければ、その時点の予想は配分せず（未配分・理由「予定未取得」）、保留を記録する。
        cur = [x for x in self.schedules.rows() if x["_seq"] == r.schedule_seq]
        sched_unavailable = (bool(cur) and cur[0].get("observed_at") is not None and cur[0]["observed_at"] > at) \
            or self._select_schedule(r, at)[1] == "none"
        if sched_unavailable:
            self.hold("schedule_unavailable_at_timepoint", key, race=rid, race_key=r.key, tp=tp, at=fmt(at),
                      msg="判断時点で利用できる予定がない（未来に観測される予定へ戻らない）")
            return self._commit_prediction(r, rid, tp, key, at, unknown_result, {
                "T": None, "bets": [{"combo": c, "state": "未配分", "amount": None, "reason": "予定未取得"} for c in K.BETS],
                "race_state": "未配分", "reasons": ["予定未取得"], "conf": None, "cap": None, "rate": None, "marks": dict(K.MARKS),
                "refs": [], "rebuild": False, "rebuild_reason": None})
        T0 = self.schedule(rid, tp)
        if at < T0:
            # KD-04：確定保存の時刻が予定時点より前。予定時点までの観測には保存より未来のものが入りうるので、保存せず保留して矛盾を記録する。
            self.hold("save_before_timepoint", key, race=rid, race_key=r.key, tp=tp, at=fmt(at), timepoint=fmt(T0),
                      msg="確定保存の時刻が予定時点より前（保存より未来の観測を使えないため保留）")
            return {"status": "held"}
        ents = [e for e in self.body.rows() if e["kind"] == "entries" and e.get("race_key") == r.key and e["avail"] <= at]
        if ents and len(ents[-1]["horses"]) < ents[-1]["field_size"]:
            self.hold("entries_incomplete", key, race=rid, tp=tp, missing=sorted(set(range(1, ents[-1]["field_size"] + 1)) - set(ents[-1]["horses"])))
            return {"status": "held"}
        view = self.decision_view(rid, tp)
        T = view["T"]
        scratched = {s["horse"]: s for s in self._active_scratches(T) if s.get("race_key") == r.key}
        bets_list, marks, rebuild, rebuild_reason = list(K.BETS), dict(K.MARKS), False, None
        if K.AXIS in scratched:
            bets_list = list(K.REBUILD_BETS)
            marks = {"◎": K.REBUILD_AXIS, "○": 3, "⭐": 4}
            if tp == K.FINAL_TIMEPOINT:
                rebuild = True
                s = scratched[K.AXIS]
                rebuild_reason = "例外の組み直し：軸馬%dの%s（発表%s）" % (K.AXIS, s["type"], fmt(s["announce"]))
        bets, reasons, cap, conf, rate, refs = [], list(view["reasons"]), None, None, None, []
        values = view.get("values")
        cancel_in_odds = set()
        if values is not None:   # KR7-01：空の辞書でも「成功した観測」。観測なし（None）と区別して全集合の欠損率を記録する
            cancel_in_odds = {c for c, v in values.items() if v in ("------", "******")}
            # KD-01：分母は届いた行ではなく、出走頭数から作る有効な組合せの全集合。行が届かなかった組も真欠損に数える。
            # 出走表にない馬番・重複・形式外のキーは分母に入れない。
            universe = all_combos(r.field_size)
            valid = [c for c in universe if c not in cancel_in_odds and not set(horses_of(c)) & set(scratched)]
            miss = [c for c in valid if c not in values or values[c] == "" or (values[c] not in ("000000",) and not _is_num(values[c]))]
            rate = [len(miss), len(valid)]
            if len(miss) * 100 > K.MISSING_HIGH_PCT * len(valid):
                conf, reasons = "Z", ["欠損"]
            elif len(miss) * 100 > K.MISSING_LOW_PCT * len(valid):
                cap = "C"
                self.warn("missing_rate", key, race=rid, tp=tp, rate=rate, row_time=fmt(view["obs"]["ended"]))
            o = view["obs"]
            refs = [{"obs_id": o["obs_id"], "content_id": o["content_id"], "target": o["target"], "stage": o["stage"],
                     "announce": o["announce"], "ended": o["ended"]}]
        for c in bets_list:
            hs = set(horses_of(c))
            sc = sorted(hs & set(scratched))
            if sc:
                bets.append({"combo": c, "state": "取消", "amount": None, "reason": "取消馬%d" % sc[0]})
            elif c in cancel_in_odds:
                bets.append({"combo": c, "state": "取消", "amount": None, "reason": "オッズ表記が取消"})
            elif values is None or conf == "Z":
                bets.append({"combo": c, "state": "未配分", "amount": None, "reason": (reasons or ["未取得"])[0]})
            elif values.get(c, "") == "" or (values[c] != "000000" and not _is_num(values[c])):
                bets.append({"combo": c, "state": "未配分", "amount": None, "reason": "欠損"})
            elif values[c] == "000000":
                bets.append({"combo": c, "state": "未配分", "amount": None, "reason": "票なし"})
            elif decision == "none_meets":
                bets.append({"combo": c, "state": "見送り", "amount": 0, "reason": "期待値が基準に届かない"})
            else:
                bets.append({"combo": c, "state": "配分済み", "amount": K.AMOUNT, "reason": None})
        states = {b["state"] for b in bets}
        if "配分済み" in states:
            race_state = "配分済み"
        elif decision == "none_meets" and "見送り" in states:
            race_state = "見送り"
        elif states == {"取消"}:
            race_state = "取消"
        else:
            race_state = "未配分"
        if race_state == "未配分" and not reasons:
            reasons = sorted({b["reason"] for b in bets if b["state"] == "未配分"})
        return self._commit_prediction(r, rid, tp, key, at, unknown_result, {
            "T": T, "bets": bets, "race_state": race_state, "reasons": reasons, "conf": conf, "cap": cap, "rate": rate,
            "marks": marks, "refs": refs, "rebuild": rebuild, "rebuild_reason": rebuild_reason})

    def _commit_prediction(self, r, rid, tp, key, at, unknown_result, c):
        race_state, reasons = c["race_state"], c["reasons"]
        pred = {"pred_id": "pred-%s-%s" % (r.key, tp), "race": rid, "race_key": r.key, "tp": tp, "tp_time": c["T"], "method": K.METHOD,
                "version": K.VERSION, "mode": "forward", "at": at, "bets": c["bets"], "race_state": race_state,
                "reasons": reasons if race_state == "未配分" else [], "confidence": c["conf"], "confidence_cap": c["cap"],
                "missing_rate": c["rate"], "marks": c["marks"], "evaluations": {h: "架空の一言評価" for h in range(1, K.FIELD_SIZE + 1)},
                "refs": c["refs"], "rebuild": c["rebuild"], "rebuild_reason": c["rebuild_reason"]}
        pred["hash"] = H({k: v for k, v in pred.items()})
        # KR8-01：適格性の行を書込みの前に組み立てる。途中で失敗しても、予想だけが保存されて適格性がない状態を残さない。
        elig = self._eligibility_row(pred, at, "確定保存")
        # KR9-01：保存段階。予想→索引→確定時刻→適格性の順に書き、警告は最後に出す。途中で失敗したら途中状態を notes に記録して
        # 例外を伝える（隠さない）。同じ確定の再試行では _repair_confirm が欠けた適格性・確定時刻・警告を補って整合させる。
        try:
            self.predictions.append(pred)
            self._pred_index[key] = pred["pred_id"]
            if tp == K.FINAL_TIMEPOINT:
                r.final_at = at
            self.eligibility.append(elig)
            if race_state == "未配分":
                self.warn("unallocated", key, race=rid, tp=tp, reasons=pred["reasons"], row_time=fmt(at))
        except Exception as ex:
            self._note_safely({"kind": "confirm_incomplete", "pred_id": pred["pred_id"], "race_key": r.key, "tp": tp, "error": repr(ex)})
            raise
        return {"status": "unknown" if unknown_result else "saved"}

    def _note_safely(self, row):
        try:
            self.notes.append(row)
        except Exception:
            pass

    def _repair_confirm(self, r, tp, key):
        """KR9-01：索引にある予想について、予想・確定時刻・適格性・未配分の警告の整合を確かめ、欠けていれば補う。
        整合済みなら何も書かずに exists を返す。予想本文は書き換えない。"""
        pid = self._pred_index[key]
        preds = [p for p in self.predictions.rows() if p["pred_id"] == pid]
        if not preds:
            # 索引だけが残る状態は今の書込み順では生じない。生じたら黙って作り直さず、保留にして知らせる。
            self.hold("confirm_index_without_prediction", key, race=r.id, tp=tp, pred_id=pid)
            return {"status": "held"}
        pred, fixed = preds[-1], []
        if tp == K.FINAL_TIMEPOINT and r.final_at is None:
            r.final_at = pred["at"]; fixed.append("final_at")
        if not any(e["pred_id"] == pid for e in self.eligibility.rows()):
            self.eligibility.append(self._eligibility_row(pred, pred["at"], "確定保存（再試行で修復）")); fixed.append("eligibility")
        if pred["race_state"] == "未配分" and ("unallocated",) + tuple(key) not in self._warn_keys:
            self.warn("unallocated", key, race=r.id, tp=tp, reasons=pred["reasons"], row_time=fmt(pred["at"])); fixed.append("unallocated_warning")
        if fixed:
            self.notes.append({"kind": "confirm_repaired", "pred_id": pid, "race_key": r.key, "tp": tp, "fixed": fixed})
        return {"status": "exists", "repaired": fixed}

    # ------------------------------------------------------------ 精算（払戻レコードだけを根拠にする）
    def _settle(self, hr):
        r = self.race_by_key[hr["race_key"]]   # KD-03／KR7-03：入口で解決済みの日付つき識別子で精算対象を選ぶ
        pred = self._final_pred(r)
        if not pred:
            return
        k = (hr["race"], H({x: hr[x] for x in hr if x != "_seq"}))
        if k in self._settle_keys:
            return
        self._settle_keys.add(k)
        bets = [b for b in pred["bets"] if b["amount"]]
        total = sum(b["amount"] for b in bets)
        if hr["not_established"]:
            refund, payout, basis = total, 0, "不成立フラグ"
        else:
            scratched = {s["horse"] for s in self._rows_of("scratch", "settle") if s.get("race_key") == r.key
                         and not any(w["scratch_seq"] == s["_seq"] for w in self.withdrawals.rows())}
            in_bets = {h for b in bets for h in horses_of(b["combo"])}
            refund_h = set(hr["refund_horses"]) if hr["refund_flag"] else set()
            if (scratched & in_bets) - refund_h:
                self.hold("settle_mismatch", (r.key, hr["saved"]), race=r.id)
                self.warn("settle_mismatch", (r.key,), race=r.id, msg="取消と払戻レコードが不一致", row_time=fmt(hr["saved"]))
                return
            refund = sum(b["amount"] for b in bets if set(horses_of(b["combo"])) & refund_h)
            payout = sum(b["amount"] // 100 * hr["trio"]["pay"] for b in bets
                         if hr["trio"] and b["combo"] == hr["trio"]["combo"] and not set(horses_of(b["combo"])) & refund_h)
            basis = "返還フラグ・返還馬番" if refund_h else "払戻レコード"
        self.settlements.append({"race": r.id, "pred_id": pred["pred_id"], "refund": refund, "invest": total - refund,
                                 "payout": payout, "basis": basis, "hr_seq": hr["_seq"] if "_seq" in hr else None,
                                 "type": "訂正" if hr["correction"] else "元"})

    # ------------------------------------------------------------ 過去再構成モード
    def hist_import(self, ev):
        self._redelivery = False   # KR9-02：操作ごとに再配送の文脈を閉じる
        today = ev["today"]
        obs_id = self.new_obs_id()
        if self.halted:
            # KR7-02：停止中は過去再構成の取込みも採用しない（観測の記録と隔離だけ）
            self.obs.append({"kind": "hist", "obs_id": obs_id, "target": "hist", "ended": today, "result": "quarantined", "note": "取得停止中"})
            for x in ev["results"]:
                self.quarantine_row("取得停止中（時計ずれ）", {"race_date": x["race_date"].strftime("%Y-%m-%d"), "race_no": x["race_no"]})
            return
        self.obs.append({"kind": "hist", "obs_id": obs_id, "target": "hist", "ended": today, "result": "success"})
        rows = [dict(x, saved=today) for x in ev["results"]]
        if ev.get("tamper"):
            rows.append(dict(ev["tamper"]))
        for x in rows:
            row = {"race_date": x["race_date"].strftime("%Y-%m-%d"), "race_no": x["race_no"], "saved": x["saved"],
                   "obs_id": obs_id, "basis": "再構成"}
            if x["saved"] != today:            # 観測ログの試行時刻と合わない（時刻の偽装）
                self.quarantine_row("保存時刻が観測ログの試行時刻と不一致", row)
                continue
            self.hist_rows.append(row)

    def hist_predict(self, rid, want_odds=False):
        r = self.races[rid]
        feats = [h for h in self.hist_rows.rows() if h["race_date"] < r.date.strftime("%Y-%m-%d")]
        self.hist_preds.append({"race": rid, "mode": "historical", "features": feats, "odds_refs": [],
                                "note": "出走表は最終状態", "want_odds": want_odds})

    # ------------------------------------------------------------ 集計（前向きと過去再構成は別）
    def latest_eligibility(self):
        """KR8-02：予想ごとに、判定時点が最も遅い行（同じなら処理順が後の行）を最新とする。"""
        out = {}
        for e in self.eligibility.rows():
            cur = out.get(e["pred_id"])
            if cur is None or (e["judged_at"], e["_seq"]) >= (cur["judged_at"], cur["_seq"]):
                out[e["pred_id"]] = e
        return out

    def aggregate(self):
        fw = [p for p in self.predictions.rows() if p["mode"] == "forward"]
        le = self.latest_eligibility()
        a = {"official": 0, "provisional": 0, "unconfirmed": 0, "ineligible": 0, "skip": 0, "unallocated": 0, "items": []}
        for p in fw:
            e = le.get(p["pred_id"])
            v = e["verdict"] if e else "未確認"
            if v == "適格" and e["basis"] == "実際":
                a["official"] += 1
            elif v == "適格":
                a["provisional"] += 1
            elif v == "不適格":
                a["ineligible"] += 1
            else:
                a["unconfirmed"] += 1
            if p["race_state"] == "見送り":
                a["skip"] += 1
            if p["race_state"] == "未配分":
                a["unallocated"] += 1
            a["items"].append({"mode": p["mode"], "pred_id": p["pred_id"]})
        h = {"items": [{"mode": p["mode"]} for p in self.hist_preds.rows()], "recovery": None,
             "note": "出走表は最終状態" if len(self.hist_preds) else None}
        return {"forward": a, "historical": h}


def _is_num(v):
    try:
        float(v)
        return v.replace(".", "", 1).isdigit()
    except (TypeError, ValueError):
        return False


def _uniq(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out
