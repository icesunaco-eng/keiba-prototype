"""架空データの取得アダプタ。手で書いた行（試験ケースの events）を、本物と同じ形にして System に流す。
JV-Link・無料ルートのアダプタは含めない。通信しない。値は種（名前）から決定的に作る。
"""
import hashlib
from datetime import datetime

from .system import System, BASE_DATE, all_combos, horses_of
from . import config as K


def T(s):
    """'HH:MM[:SS]' は 2099-01-10、'MM-DD HH:MM[:SS]' は 2099年、'YYYY-MM-DD HH:MM[:SS]' は完全指定。"""
    if s is None:
        return None
    s = s.strip()
    if len(s.split(" ")) == 1 and ":" in s:
        s = "2099-01-10 " + s
    elif len(s.split(" ")[0]) == 5:
        s = "2099-" + s
    if len(s) == 10:
        return datetime.strptime(s, "%Y-%m-%d")
    fmt = "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(s, fmt)


def make_values(seed, mods=None, n=K.FIELD_SIZE):
    combos = all_combos(n)
    vals = {}
    for c in combos:
        x = int(hashlib.sha256(("%s|%s" % (seed, c)).encode()).hexdigest()[:6], 16)
        vals[c] = "%.1f" % (5.0 + (x % 5000) / 10.0)
    mods = mods or {}

    def pick(count, include):
        chosen = list(include)
        for c in reversed(combos):
            if len(chosen) >= count:
                break
            if c in chosen or c in K.BETS or c in used:
                continue
            chosen.append(c)
        return chosen
    used = set()
    if "cancel_horse" in mods:
        for c in combos:
            if mods["cancel_horse"] in horses_of(c):
                vals[c] = "******"
                used.add(c)
    for key, marker in (("missing", ""), ("invalid", "ab"), ("novote", "000000")):
        if key in mods:
            for c in pick(mods[key], mods.get(key + "_include", [])):
                vals[c] = marker
                used.add(c)
    for c, v in (mods.get("set") or {}).items():
        vals[c] = "%.1f" % v
    return vals


class FictionalAdapter:
    def __init__(self, system=None):
        self.sys = system or System()

    def apply(self, ev):
        s, op = self.sys, ev["op"]
        if op == "odds":
            race = s.races.get(ev["race"])
            raw = ev.get("announce_raw")
            if raw is None and ev.get("announce"):
                raw = T(ev["announce"]).strftime("%m%d%H%M")
            vals = make_values(ev["values"], ev.get("mods")) if ev.get("values") else None
            s.ingest_odds({
                "race": ev["race"], "stage": ev.get("stage"), "ended": T(ev["end"]), "announce_raw": raw, "values": vals,
                "result": ev.get("result", "success"), "skew": ev.get("skew", 0.0),
                "obs_id": ev["obs_id"] if "obs_id" in ev else s.new_obs_id(),
                "target": ev["target"] if "target" in ev else (race.key if race else None),
                "adapter": ev.get("adapter", "fictional"), "sale": ev.get("sale")})
        elif op == "row":
            e = dict(ev)
            for k in ("saved", "announce"):
                if k in e and e[k] is not None:
                    e[k] = T(e[k])
            for k in ("old", "new", "actual"):
                if k in e and not (k == "old" and e["kind"] == "track"):
                    e[k] = T(e[k])
            if "created" in e:
                e["created"] = T(e["created"])
            if "race_date" in e:
                e["race_date"] = T(e["race_date"])
            s.ingest_row(e)
        elif op == "fetch_day":
            e = dict(ev, date=T(ev["date"]), ended=T(ev["end"]))
            e["scratches"] = [dict(x, announce=T(x["announce"])) for x in ev["scratches"]]
            s.fetch_day(e)
        elif op == "confirm":
            return s.confirm(ev["race"], ev["tp"], T(ev["at"]), ev.get("decision", "default"), ev.get("unknown_result", False))
        elif op == "judge_clock":
            s.judge_clock = {"independent": ev["independent"], "trusted": ev["trusted"]}
        elif op == "view":
            s.decision_view(ev["race"], ev["tp"])
        elif op == "add_race":
            d = T(ev["date"])
            s.add_race(ev["race"], d, T(ev["date"] + " " + ev["post"]))
        elif op == "hist_import":
            res = [{"race_date": T(x["race_date"]), "race_no": x["race_no"]} for x in ev["results"]]
            tam = ev.get("tamper")
            if tam:
                tam = {"race_date": T(tam["race_date"]), "race_no": tam["race_no"], "saved": T(tam["saved"])}
            s.hist_import({"today": T(ev["today"]), "results": res, "tamper": tam})
        elif op == "hist_predict":
            s.hist_predict(ev["race"], ev.get("want_odds", False))
        else:
            raise ValueError("未知のop " + op)
