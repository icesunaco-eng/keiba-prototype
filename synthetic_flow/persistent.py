"""Isolated synthetic event journal. Never accepts real data or places orders."""
import sqlite3,json,hashlib
from pathlib import Path
from datetime import datetime
from demo import Bridge

def encode(x):
 if isinstance(x,datetime):return {'__datetime__':x.isoformat()}
 raise TypeError('unsupported type')
def decode(x):
 if set(x)=={'__datetime__'}:return datetime.fromisoformat(x['__datetime__'])
 return x
def digest(value):
 return hashlib.sha256(json.dumps(value,default=encode,sort_keys=True,allow_nan=False).encode()).hexdigest()
def code_binding():
 p=Path(__file__).resolve().parent
 files=[p/'model.py',p/'demo.py',p/'persistent.py']+sorted((p/'src').rglob('*.py'))
 return {str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
def evidence(bridge):
 return {'predictions':bridge.system.predictions.rows(),'results':bridge.results.rows(),'comparisons':bridge.comparisons.rows()}
class Journal:
 def __init__(self,path):self.path=str(path)
 def read_saved(self):
  """Read persisted evidence WITHOUT loading/recomputing old predictions."""
  db=None
  try:
   uri=Path(self.path).resolve().as_uri()+'?mode=ro'
   db=sqlite3.connect(uri,uri=True)
   events=[]
   for (payload,) in db.execute('SELECT payload FROM events ORDER BY seq'):
    event=json.loads(payload,object_hook=decode)
    check={k:v for k,v in event.items() if k!='evidence_hash'}
    if event.get('schema')!=2 or event.get('evidence_hash')!=digest(check):raise ValueError('unverified journal format')
    events.append(event)
   return {'state':'read_only','events':events,'recomputed':False}
  except Exception as ex:return {'state':'held','reason':type(ex).__name__}
  finally:
   if db is not None:db.close()
 def apply(self,rid,request,result=None):
  db=None
  try:
   db=sqlite3.connect(self.path,timeout=1)
   db.execute('PRAGMA synchronous=FULL')
   db.execute('BEGIN IMMEDIATE')
   db.execute('CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
   binding=code_binding()
   bridge=Bridge()
   for (payload,) in db.execute('SELECT payload FROM events ORDER BY seq'):
    event=json.loads(payload,object_hook=decode)
    check={k:v for k,v in event.items() if k!='evidence_hash'}
    if event.get('schema')!=2 or event.get('evidence_hash')!=digest(check):raise ValueError('unverified journal format')
    if event['code_binding']!=binding:raise ValueError('code version mismatch; saved evidence preserved')
    rr=event['request'];bridge.system.add_race(event['rid'],rr['scheduled_start'],rr['scheduled_start'])
    saved=bridge.save(event['rid'],rr)
    if saved['state'] not in ('saved','exists'):raise ValueError('journal replay rejected')
    if event['result'] is not None and bridge.compare(saved['pred_id'],event['result'])['state'] not in ('compared','exists'):raise ValueError('journal result replay rejected')
    if event['saved_evidence']!=evidence(bridge):raise ValueError('replay differs from fixed evidence')
   # Only new synthetic packets can reach the memory bridge. All errors roll back.
   if type(request) is not dict or request.get('synthetic') is not True:raise ValueError('synthetic only')
   bridge.system.add_race(rid,request['scheduled_start'],request['scheduled_start'])
   saved=bridge.save(rid,request)
   if saved['state'] not in ('saved','exists'):raise ValueError('prediction rejected')
   compared=None
   if result is not None:
    compared=bridge.compare(saved['pred_id'],result)
    if compared['state'] not in ('compared','exists'):raise ValueError('result rejected')
   changed=saved['state']=='saved' or (compared is not None and compared['state']=='compared')
   if changed:
    event={'schema':2,'rid':rid,'request':request,'result':result,'input_hash':digest(request),'code_binding':binding,'saved_evidence':evidence(bridge)}
    event['evidence_hash']=digest(event)
    payload=json.dumps(event,default=encode,sort_keys=True,allow_nan=False)
    db.execute('INSERT INTO events(payload) VALUES (?)',(payload,))
   count=db.execute('SELECT count(*) FROM events').fetchone()[0]
   db.commit()
   return {'state':'committed' if changed else 'exists','events':count,'predictions':len(bridge.system.predictions),'comparisons':len(bridge.comparisons),'synthetic_only':True,'official_performance_eligible':False,'order_created':False}
  except Exception as ex:
   if db is not None:db.rollback()
   return {'state':'held','reason':type(ex).__name__,'synthetic_only':True,'order_created':False}
  finally:
   if db is not None:db.close()
