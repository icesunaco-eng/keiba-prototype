"""受け入れ基準の「ふつうの運用で起こりうる入力」を、すべての取得・原本の場所に入れる（本物の入口から）。"""
import json
import types
import urllib.error
from datetime import datetime

import pytest
from keiba import cli
from keiba.ledger import Ledger
from keiba.live import collector as c, jra, mam, wallet
from keiba.live.collector import JST, Collector
from tests.test_live_wallet import RK, _setup, card_html, result_html, trio_html, win_html

LIST="pw01drl0005"
def boom(): raise urllib.error.URLError("つながらない")
BAD={"取得失敗":boom,"空":lambda:"","途中":lambda:"<html><body><table><tr><td","別ページ":lambda:"<html><body>混み合っています</body></html>"}

@pytest.fixture
def env(tmp_path,monkeypatch):
    orig=jra.parse_card
    monkeypatch.setattr(jra,"parse_card",lambda p:[dict(x,horse_id=f"H{int(x['horse_number'])-1:04d}") for x in orig(p)])
    paths,col=_setup(tmp_path)
    col.state["races"][RK].update(list_cname=LIST,result_links=[["/r","R"]]); col.state["live_pending"]=True
    col.state["taken"][f"{RK}|1h"]="x"; col._write_state()
    monkeypatch.setattr(cli,"_notify",lambda *a,**k:None)
    monkeypatch.setattr(mam,"export_packets",lambda p,**k:[])
    return paths,col

def tick(paths,monkeypatch,at,where,bad):
    class Cl:
        count=0
        def get(self,path,cname):
            kind="索引" if (path,cname)==tuple(jra.ENTRY_INDEX) else "一覧" if cname==LIST else "結果" if cname=="R" else "他"
            if kind==where: return bad()
            return {"索引":f"<html><a onclick=\"doAction('/JRADB/accessD.html', '{LIST}')\">x</a></html>","一覧":"<html><body></body></html>",
                    "結果":result_html([3,1,2,4,5,6,7,8])}.get(kind,"<html></html>")
        def get_url(self,url):
            if where=="お知らせ": return bad()
            return "<html><body></body></html>"
    real=Collector
    with monkeypatch.context() as m:
        m.setattr(c,"Collector",lambda root:real(root,client=Cl(),now=lambda:at))
        m.setattr(wallet,"prediction_clock",lambda:at)
        cli.cmd_collect(paths,types.SimpleNamespace(action="tick"))

def rows(paths,w):
    if not paths.ledger_path.exists(): return []
    l=Ledger(paths.ledger_path,read_only=True); o=list(getattr(l,w)()); l.close(); return o

@pytest.mark.parametrize("bad",BAD)
@pytest.mark.parametrize("where",["索引","一覧","お知らせ"])
def test_prediction_survives_fetch_failure(env,monkeypatch,where,bad):
    paths,_=env
    tick(paths,monkeypatch,datetime(2025,8,30,14,45,tzinfo=JST),where,BAD[bad])
    assert rows(paths,"predictions")

@pytest.mark.parametrize("bad",BAD)
@pytest.mark.parametrize("where",["索引","お知らせ"])
def test_pending_settlement_survives_fetch_failure(env,monkeypatch,where,bad):
    paths,col=env
    wallet.run_live(paths)
    col._save(RK,"result","result","/r","R",result_html([3,1,2,4,5,6,7,8]))
    col.state["taken"][f"{RK}|result"]="x"; col._write_state()
    tick(paths,monkeypatch,datetime(2025,8,30,16,15,tzinfo=JST),where,BAD[bad])
    assert rows(paths,"settlements")

@pytest.mark.parametrize("bad",BAD)
@pytest.mark.parametrize("where",["索引","一覧"])
def test_known_race_still_captures_result(env,monkeypatch,where,bad):
    paths,col=env
    wallet.run_live(paths)
    tick(paths,monkeypatch,datetime(2025,8,30,16,15,tzinfo=JST),where,BAD[bad])
    if where=="一覧":   # 取得の前に一覧を読み直すので、その回は取れない。次の回に取る
        tick(paths,monkeypatch,datetime(2025,8,30,16,20,tzinfo=JST),"",None)
    assert f"{RK}|result" in json.loads((col.root/"state.json").read_text())["taken"]

@pytest.mark.parametrize("how",["無い","空"])
@pytest.mark.parametrize("kind",["card","odds_win","odds_trio","result","notice"])
def test_missing_original_does_not_stop_other_race(env,monkeypatch,kind,how):
    paths,col=env
    rk2="2025-08-30|東京|12"
    col.state["races"][rk2]=dict(col.state["races"][RK],race_number=12)
    rec={}
    for k,p in (("card",card_html([f"H{h:04d}" for h in range(8)])),("odds_win",win_html()),("odds_trio",trio_html())):
        rec[k]=col._save(rk2,k,"1h","/x","c",p)
    if kind=="result": rec[kind]=col._save(rk2,kind,"result","/x","c",result_html([1,2,3,4,5,6,7,8]))
    if kind=="notice": rec[kind]=col._save(None,kind,None,"/x","c","<html></html>")
    col._write_state()
    f=col.root/rec[kind]["path"]
    f.unlink() if how=="無い" else f.write_text("")
    wallet.run_live(paths)
    assert any(p["race_key"]==RK for p in rows(paths,"predictions"))
    from keiba.app.data import Source
    assert RK in Source(paths,"live").preds

def test_outage_notifies_once_and_recovers(env,monkeypatch):
    paths,col=env
    sent=[]
    monkeypatch.setattr(cli,"_notify",lambda *a,**k:sent.append(a))
    for m in (15,20,25):
        tick(paths,monkeypatch,datetime(2025,8,30,12,m,tzinfo=JST),"索引",boom)
    assert len(sent)==1
    tick(paths,monkeypatch,datetime(2025,8,30,12,30,tzinfo=JST),"",None)
    s=json.loads((col.root/"state.json").read_text())
    assert s["refreshed_at"].startswith("2025-08-30T12:30") and not s.get("refresh_failed")
