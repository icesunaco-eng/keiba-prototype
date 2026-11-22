"""取りやめ・精算の反例。本物の入口（collect tick）から通す。"""
import json
import types
from datetime import datetime
import pytest
from keiba.live import jra, collector as c, wallet, mam
from keiba.live.collector import Collector, JST
from keiba.ledger import Ledger
from tests.test_live_wallet import RK, _setup, result_html, card_html, win_html, trio_html

FULL = result_html([3,1,2,4,5,6,7,8])
def news(title): return f'<html><body><ul><li><div class="news_line"><a href="/n"><div class="txt">{title}</div></a></div></li></ul></body></html>'

@pytest.fixture
def env(tmp_path, monkeypatch):
    orig = jra.parse_card
    monkeypatch.setattr(jra, "parse_card", lambda p: [dict(x, horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    paths, col = _setup(tmp_path)
    wallet.run_live(paths)
    return paths, col

def rows(paths):
    led = Ledger(paths.ledger_path, read_only=True); out = list(led.settlements()); led.close(); return out

def cancel(root, start=1, to=None):
    with open(root/"cancellations.jsonl","a") as f:
        f.write(json.dumps(dict(race_date="2025-08-30",venue="東京",from_race=start,to_race=to,title="取りやめ",found_at="2025-08-30T16:00:00+09:00"),ensure_ascii=False)+"\n")

def cli_tick(paths, monkeypatch, now, pages, notice):
    from keiba import cli
    class Cl:
        count = 0
        def get(self, path, cname): return pages.get(cname, "<html></html>")
        def get_url(self, url): return notice
    real = c.Collector
    monkeypatch.setattr(c, "Collector", lambda root: real(root, client=Cl(), now=lambda: now))
    monkeypatch.setattr(real, "refresh", lambda self, force=False: None)
    monkeypatch.setattr(c.jra, "parse_race_list", lambda p: {"races": []})
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(mam, "export_packets", lambda p: [])
    cli.cmd_collect(paths, types.SimpleNamespace(action="tick"))

def test_A_real_entry_point_settles_cancellation(env, monkeypatch):
    paths, col = env
    col.state["races"][RK].update(list_cname="L"); col._write_state()
    cli_tick(paths, monkeypatch, datetime(2025,8,30,16,15,tzinfo=JST), {}, news("本日【8月30日（土曜）】の東京競馬は第8レース以降を取りやめといたします"))
    assert (col.root/"cancellations.jsonl").exists()
    assert [s for s in rows(paths) if s["status"]=="refunded_void"]

def test_B_started_race_is_not_voided_by_midday_meeting_cancel(env):
    paths, col = env
    cancel(col.root)            # 「開催を中止」＝from 1。発走15:40、告知16:00、結果は16:10から
    wallet.run_live(paths)
    col._save(RK,"result","result","/x","c",FULL)
    wallet.run_live(paths)
    assert not [s for s in rows(paths) if s["status"]=="refunded_void"]

def test_C_app_waits_for_started_race(env):
    from keiba.app.data import Source
    paths, col = env
    cancel(col.root)
    assert not (Source(paths,"live").results.get(RK) or {}).get("cancelled")

def test_D_one_missing_month_does_not_block_others(tmp_path):
    col = Collector(tmp_path, client=None, now=lambda: datetime(2026,11,30,9,0,tzinfo=JST))
    for d in ("2026-11-30","2026-12-01"):
        col.state["races"][f"{d}|東京|1"] = dict(race_date=d,venue="東京",race_number=1,post_time="10:00")
    class Cl:
        count=0
        def get_url(self,url):
            if "202612" in url: raise OSError("404")
            return news("本日【11月30日（月曜）】の東京競馬は開催を中止します")
    col.client=Cl(); col.check_notices(lambda *_:None)
    assert c.load_cancellations(tmp_path/"cancellations.jsonl")

def test_E_settlement_retried_after_failure(env, monkeypatch):
    paths, col = env
    col.state["races"][RK].update(list_cname="L", result_links=[["/x","R"]]); col._write_state()
    real = wallet.run_live
    def boom(p): raise RuntimeError("database is locked")
    monkeypatch.setattr(wallet,"run_live",boom)
    cli_tick(paths, monkeypatch, datetime(2025,8,30,16,15,tzinfo=JST), {"R":FULL}, news("お知らせ"))
    monkeypatch.setattr(wallet,"run_live",real)
    monkeypatch.undo(); orig=jra.parse_card
    monkeypatch.setattr(jra, "parse_card", lambda p: [dict(x, horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    cli_tick(paths, monkeypatch, datetime(2025,8,30,16,20,tzinfo=JST), {"R":FULL}, news("お知らせ"))
    assert rows(paths)

def test_F_purchase_list_label(env, monkeypatch):
    from keiba.app import pages
    import inspect
    assert "返還（不成立）" not in inspect.getsource(pages)

def test_G_notice_snapshot_not_duplicated(tmp_path):
    t={"now":datetime(2026,11,22,9,0,tzinfo=JST)}
    col=Collector(tmp_path,client=None,now=lambda:t["now"])
    col.state["races"]["2026-11-22|東京|1"]=dict(race_date="2026-11-22",venue="東京",race_number=1)
    class Cl:
        count=0
        def get_url(self,url): return news("お知らせ")
    col.client=Cl(); col.check_notices()
    t["now"]=datetime(2026,11,22,10,0,tzinfo=JST); col.check_notices()
    assert (tmp_path/"snapshots.jsonl").read_text().count('"notice"')==1

def test_H_void_stake_matches_final_15m_allocation(env):
    paths, col = env
    cancel(col.root, 8)
    wallet.run_live(paths, now=datetime(2025,8,30,15,0,tzinfo=JST))
    col.now=lambda: datetime(2025,8,30,15,15,tzinfo=JST)
    other=trio_html("15時14分").replace('class="odds">2','class="odds">9')
    for k,p in (("card",card_html([f"H{h:04d}" for h in range(8)])),("odds_win",win_html()),("odds_trio",other)): col._save(RK,k,"15m","/x","c",p)
    wallet.run_live(paths, now=datetime(2025,8,30,15,20,tzinfo=JST))
    assert not rows(paths)
    wallet.run_live(paths, now=datetime(2025,8,30,15,41,tzinfo=JST))
    assert rows(paths)
    led=Ledger(paths.ledger_path,read_only=True); P={(p["method"],p["version"]):p for p in led.predictions() if p["time_point"]=="15m"}; led.close()
    assert P
    for s in rows(paths):
        p=P.get((s["method"],s["version"]))
        if p: assert s["stake"]==sum(p["allocation"][s["wallet"]]["stakes"].values())

def test_I_meeting_cancel_found_before_post_is_refunded(env):
    paths, col = env
    with open(col.root/"cancellations.jsonl","w") as f:
        f.write(json.dumps(dict(race_date="2025-08-30",venue="東京",from_race=1,to_race=None,found_at="2025-08-30T08:00:00+09:00"),ensure_ascii=False)+"\n")
    wallet.run_live(paths)
    assert [s for s in rows(paths) if s["status"]=="refunded_void"]

def test_J_meeting_cancel_after_post_refunds_after_window_with_empty_page(env):
    paths, col = env
    cancel(col.root)
    col._save(RK,"result","result","/x","c","<html><body>空</body></html>")
    wallet.run_live(paths, now=datetime(2025,8,30,21,0,tzinfo=JST))
    assert not rows(paths)
    wallet.run_live(paths, now=datetime(2025,8,30,21,41,tzinfo=JST))
    assert [s for s in rows(paths) if s["status"]=="refunded_void"]

def test_K_no_link_cancel_triggers_again_at_post(env, monkeypatch):
    paths, col = env
    col.state["races"][RK].update(list_cname="L"); col.state["live_pending"]=False; col._write_state()
    title=news("本日【8月30日（土曜）】の東京競馬は開催を中止します")
    cli_tick(paths, monkeypatch, datetime(2025,8,30,15,30,tzinfo=JST), {}, title)
    assert not rows(paths)
    monkeypatch.undo(); orig=jra.parse_card
    monkeypatch.setattr(jra, "parse_card", lambda p: [dict(x, horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    cli_tick(paths, monkeypatch, datetime(2025,8,30,15,45,tzinfo=JST), {}, title)
    assert [s for s in rows(paths) if s["status"]=="refunded_void"]

def test_L_truncated_notice_list_is_retried(tmp_path):
    col=Collector(tmp_path,client=None,now=lambda:datetime(2026,11,22,9,0,tzinfo=JST))
    col.state["races"]["2026-11-22|東京|1"]=dict(race_date="2026-11-22",venue="東京",race_number=1)
    class Cl:
        count=0
        def get_url(self,url): return "<html><body>途中"
    col.client=Cl(); col.check_notices(lambda *_:None)
    assert "notices_at" not in col.state

def test_M_no_prediction_after_post_even_for_new_code_version(env, monkeypatch):
    paths, col = env
    monkeypatch.setattr(wallet, "code_hash", lambda: "ffffffff00000000")
    assert wallet.run_live(paths, now=datetime(2025,8,30,15,41,tzinfo=JST))["predicted"] == 0
    assert wallet.run_live(paths, now=datetime(2025,8,30,15,20,tzinfo=JST))["predicted"] > 0
