"""Synthetic-only snapshot interface; no network or real-source permission claim."""
import hashlib,json,copy
from datetime import datetime
ALLOWED={'horse_id','distance_m','carried_weight_kg'}
def aware(t):return isinstance(t,datetime) and t.tzinfo is not None and t.utcoffset() is not None
def text_ok(value):
 if type(value) is not str or not value.strip():return False
 try:value.encode('utf-8')
 except UnicodeEncodeError:return False
 return True
class Store:
 def __init__(self):self._rows={}
 def rows(self):return copy.deepcopy(list(self._rows.values()))
 def ingest(self,packet):
  held=lambda reason:{'state':'held','reason':reason,'allocation_allowed':False}
  keys={'synthetic','race_key','source','observed_at','published_at','as_of','scheduled_start','rows','odds_status'}
  if type(packet) is not dict or set(packet)!=keys:return held('invalid_envelope')
  if packet['synthetic'] is not True:return held('real_data_permission_unverified')
  for k in ('race_key','source'):
   if not text_ok(packet[k]):return held('invalid_identity')
  times=('observed_at','published_at','as_of','scheduled_start')
  if not all(aware(packet[k]) for k in times):return held('invalid_time')
  if max(packet['observed_at'],packet['published_at'])>packet['as_of'] or packet['as_of']>=packet['scheduled_start']:return held('not_available_prestart')
  if packet['odds_status'] not in ('not_on_sale','missing','available'):return held('invalid_odds_status')
  rows=packet['rows']
  if type(rows) is not list or not rows:return held('missing_rows')
  ids=set()
  for row in rows:
   if type(row) is not dict or set(row)!=ALLOWED:return held('unknown_or_missing_column')
   ident=row['horse_id']
   if not text_ok(ident) or ident in ids:return held('invalid_horse_id')
   ids.add(ident)
   if type(row['distance_m']) is not int or not 100<=row['distance_m']<=10000:return held('invalid_distance')
   w=row['carried_weight_kg']
   if type(w) not in (int,float) or not 0<w<100:return held('invalid_weight')
  canonical=json.dumps(packet,sort_keys=True,ensure_ascii=False,default=lambda t:t.isoformat(),allow_nan=False)
  digest=hashlib.sha256(canonical.encode()).hexdigest()
  state='duplicate' if digest in self._rows else 'stored'
  self._rows.setdefault(digest,{'content_hash':digest,'synthetic':True,'source_authenticity_verified':False,'real_usage_permission_verified':False,'packet':copy.deepcopy(packet)})
  return {'state':state,'content_hash':digest,'allocation_allowed':False,'allocation_state':{'not_on_sale':'unallocated_not_on_sale','missing':'unallocated_missing_odds','available':'unallocated_not_implemented'}[packet['odds_status']]}
