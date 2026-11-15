"""Synthetic-only bridge to existing System append-only prediction storage.
Does NOT call System.confirm (dummy allocation), eligibility, or cash settlement.
"""
import sys,json,hashlib,copy
from pathlib import Path
from datetime import datetime,timezone,timedelta
P=Path(__file__).resolve().parent
attempts=[]
def audit(e,a):
 if e.startswith(('socket.','subprocess.','os.system','os.posix_spawn')):
  attempts.append(e);raise RuntimeError('external action denied')
sys.addaudithook(audit)
sys.path[:0]=[str(P),str(P/'src')]
from model import predict,GROUPS
from keiba_proto.system import System,AppendOnly,H
METHOD='synthetic-score-pl-v1'
class Bridge:
 def __init__(self):
  self.system=System();self.results=AppendOnly('synthetic_results');self.comparisons=AppendOnly('synthetic_comparisons')
 def save(self,rid,request):
  output=predict(request)
  if output['state']=='held':return output
  race=self.system.races.get(rid)
  if race is None or race.post!=request['scheduled_start']:return {'state':'held','reason':'race_schedule_mismatch'}
  key=H({'race_key':race.key,'method':METHOD,'request':request})
  prior=[p for p in self.system.predictions.rows() if p['pred_id']==key]
  if prior:return {'state':'exists','pred_id':key}
  row={'pred_id':key,'race':rid,'race_key':race.key,'method':METHOD,'version':'isolated-v1','mode':'synthetic_only','tp':'demo','at':request['as_of'],'input_hash':H(request),'output':output,'bets':[],'official_performance_eligible':False}
  self.system.predictions.append(row)
  return {'state':'saved','pred_id':key}
 def compare(self,pred_id,result):
  if type(result) is not dict or set(result)!={'synthetic','race_key','available_at','top3'} or result.get('synthetic') is not True:return {'state':'held'}
  ps=[p for p in self.system.predictions.rows() if p['pred_id']==pred_id]
  if len(ps)!=1:return {'state':'held'}
  p=ps[0];t=result['available_at'];r=self.system.race_by_key[p['race_key']]
  if not isinstance(t,datetime) or t.tzinfo is None or t.utcoffset() is None or t<r.post or result['race_key']!=p['race_key']:return {'state':'held'}
  top=result['top3'];ids=set(p['output']['scores'])
  if type(top) is not list or len(top)!=3 or any(type(x) is not str or x not in ids for x in top) or len(set(top))!=3:return {'state':'held'}
  # Trio is unordered: normalize only its three horse IDs, not other evidence.
  result=dict(result,top3=sorted(top))
  key=H({'prediction':pred_id,'result':result})
  if any(x['key']==key for x in self.comparisons.rows()):return {'state':'exists'}
  # Contradictory later result is held; no replacement policy invented.
  if any(x['pred_id']==pred_id for x in self.comparisons.rows()):return {'state':'held','reason':'conflicting_result'}
  self.results.append(result)
  candidates=p['output']['trio_candidates'];rank=next(i+1 for i,c in enumerate(candidates) if set(c['horses'])==set(top))
  self.comparisons.append({'key':key,'pred_id':pred_id,'synthetic':True,'actual_trio':sorted(top),'candidate_rank':rank,'top1_match':rank==1,'official_performance_eligible':False,'profit':None,'stake':None})
  return {'state':'compared','rank':rank}

def run():
 t=datetime(2140,4,11,10,tzinfo=timezone.utc)
 req={'synthetic':True,'as_of':t,'scheduled_start':t+timedelta(minutes=15),'horses':[{'id':str(i),'features':{g:{'value':9-i,'observed_at':t,'available_at':t,'source':'fabricated','target_outcome':False} for g in GROUPS}} for i in range(1,5)]}
 b=Bridge();b.system.add_race('SYN01',t,req['scheduled_start']);saved=b.save('SYN01',req);assert saved['state']=='saved';assert b.save('SYN01',req)['state']=='exists'
 result={'synthetic':True,'race_key':'2140-04-11/SYN01','available_at':t+timedelta(minutes=20),'top3':['1','2','3']}
 assert b.compare(saved['pred_id'],result)=={'state':'compared','rank':1}
 assert b.compare(saved['pred_id'],result)['state']=='exists'
 assert b.compare(saved['pred_id'],dict(result,top3=['3','1','2']))['state']=='exists'
 from datetime import tzinfo
 class UnknownOffset(tzinfo):
  def utcoffset(self,dt):return None
 assert b.compare(saved['pred_id'],dict(result,available_at=t.replace(tzinfo=UnknownOffset())))['state']=='held'
 bad=copy.deepcopy(req);bad['synthetic']=False;assert b.save('SYN01',bad)['state']=='held'
 bad=copy.deepcopy(req);bad['horses'][0]['features']['ability']['available_at']=t+timedelta(seconds=1);assert b.save('SYN01',bad)['state']=='held'
 for change in ({'synthetic':False},{'available_at':t},{'top3':['1','1','2']},{'race_key':'other'},{'top3':['2','3','4']}):
  assert b.compare(saved['pred_id'],dict(result,**change))['state']=='held'
 assert len(b.system.predictions)==len(b.results)==len(b.comparisons)==1
 assert len(b.system.settlements)==len(b.system.eligibility)==0
 assert not attempts
 return {'synthetic_only':True,'predictions':b.system.predictions.rows(),'comparisons':b.comparisons.rows(),'cash_settlements':0,'eligibility_rows':0,'external_attempts':attempts,'passed':True}
if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False,default=str,indent=2))
