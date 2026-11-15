"""Synthetic wiring; scores are separately fabricated, never inferred from result data."""
import importlib.util,sys,json,copy
from pathlib import Path
from datetime import datetime,timezone,timedelta
P=Path(__file__).resolve().parent
attempts=[]
def audit(e,a):
 if e.startswith(('socket.','subprocess.','os.system','os.posix_spawn')):attempts.append(e);raise RuntimeError('denied')
sys.addaudithook(audit)
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
S=load('snapshot',P/'snapshot.py');M=load('score_model',P.parent/'score-prototype/model.py')
def connect(store,packet,request):
 saved=store.ingest(packet)
 if saved['state']=='held':return saved
 # Snapshot 3 columns cannot generate the eight scored features: no invented conversion.
 if request is None:return {'state':'held','reason':'scored_features_unavailable','snapshot_hash':saved['content_hash']}
 if type(request) is not dict or type(request.get('horses')) is not list:return {'state':'held','reason':'invalid_model_input'}
 if type(request.get('race_key')) is not str or request['race_key']!=packet['race_key']:return {'state':'held','reason':'snapshot_model_race_mismatch'}
 if request.get('as_of')!=packet['as_of'] or request.get('scheduled_start')!=packet['scheduled_start']:return {'state':'held','reason':'snapshot_model_time_mismatch'}
 try:ids={h['id'] for h in request['horses']}
 except (TypeError,KeyError):return {'state':'held','reason':'invalid_model_identity'}
 if ids!={r['horse_id'] for r in packet['rows']}:return {'state':'held','reason':'snapshot_model_horse_mismatch'}
 # Race binding is checked at the adapter boundary; model schema stays unchanged.
 model_request={k:v for k,v in request.items() if k!='race_key'}
 output=M.predict(model_request)
 return {'state':output['state'],'snapshot_hash':saved['content_hash'],'model_output':output,'allocation_state':saved['allocation_state'],'allocation_allowed':False,'feature_derivation':'separate_fabricated_scores_not_derived_from_snapshot'}
def run():
 t=datetime(2140,4,11,10,tzinfo=timezone.utc)
 packet=dict(synthetic=True,race_key='2140-04-11/SYN01',source='fabricated',observed_at=t,published_at=t,as_of=t,scheduled_start=t+timedelta(minutes=15),rows=[dict(horse_id=str(i),distance_m=1600,carried_weight_kg=55) for i in range(1,5)],odds_status='not_on_sale')
 req=dict(race_key=packet['race_key'],synthetic=True,as_of=t,scheduled_start=packet['scheduled_start'],horses=[{'id':str(i),'features':{g:dict(value=9-i,observed_at=t,available_at=t,source='fabricated-test-only',target_outcome=False) for g in M.GROUPS}} for i in range(1,5)])
 store=S.Store();assert connect(store,packet,None)['reason']=='scored_features_unavailable'
 out=connect(store,packet,req);assert out['state']=='synthetic_rule_prototype';assert out['model_output']['trio_candidates'][0]['horses']==['1','2','3'];assert not out['allocation_allowed'];assert out['allocation_state']=='unallocated_not_on_sale'
 assert connect(store,packet,req)['snapshot_hash']==out['snapshot_hash'];assert len(store.rows())==1
 for field,value in [('as_of',t-timedelta(seconds=1)),('scheduled_start',t+timedelta(hours=1))]:
  bad=copy.deepcopy(req);bad[field]=value;assert connect(store,packet,bad)['state']=='held'
 bad=copy.deepcopy(req);bad['horses'].pop();assert connect(store,packet,bad)['state']=='held'
 bad=copy.deepcopy(packet);bad['synthetic']=False;assert connect(store,bad,req)['state']=='held'
 bad=copy.deepcopy(packet);bad['rows'][0]['payout']=100;assert connect(store,bad,req)['state']=='held'
 for other in (None,'2140-04-11/OTHER'):
  bad=copy.deepcopy(req);bad['race_key']=other;assert connect(store,packet,bad)['state']=='held'
 bad=copy.deepcopy(packet);bad['race_key']='2140-04-11/OTHER';assert connect(store,bad,req)['state']=='held'
 bad=copy.deepcopy(req);del bad['race_key'];assert connect(store,packet,bad)['state']=='held'
 assert not attempts
 return dict(passed=True,snapshot_count=len(store.rows()),external_attempts=attempts,output=out)
if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False,default=str,indent=2))
