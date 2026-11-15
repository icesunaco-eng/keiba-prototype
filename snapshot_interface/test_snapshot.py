import importlib.util,pathlib,unittest,copy
from datetime import datetime,timedelta,timezone,tzinfo
s=importlib.util.spec_from_file_location('snapshot',pathlib.Path(__file__).with_name('snapshot.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
T=datetime(2140,4,11,10,tzinfo=timezone.utc)
def fixture():return dict(synthetic=True,race_key='2140-04-11/SYN01',source='fabricated-only',observed_at=T,published_at=T,as_of=T,scheduled_start=T+timedelta(minutes=15),rows=[dict(horse_id='1',distance_m=1600,carried_weight_kg=55)],odds_status='not_on_sale')
class Test(unittest.TestCase):
 def test_store(self):
  a=m.Store();p=fixture();x=a.ingest(p);self.assertEqual(x['state'],'stored');self.assertEqual(x['allocation_state'],'unallocated_not_on_sale');self.assertFalse(x['allocation_allowed']);self.assertEqual(a.ingest(p)['state'],'duplicate');p['source']='changed';self.assertEqual(a.rows()[0]['packet']['source'],'fabricated-only');r=a.rows();r[0]['packet']['source']='mutated';self.assertEqual(a.rows()[0]['packet']['source'],'fabricated-only')
 def test_columns(self):
  for field in ('result','payout','winner','odds','nested'):
   p=fixture();p['rows'][0][field]=1;a=m.Store();self.assertEqual(a.ingest(p)['state'],'held');self.assertEqual(a.rows(),[])
 def test_envelope(self):
  for k,v in [('synthetic',False),('synthetic',1),('source',' '),('as_of',None),('as_of',T+timedelta(minutes=15)),('published_at',T+timedelta(seconds=1)),('observed_at',T+timedelta(seconds=1)),('odds_status','unknown'),('rows',[])]:
   p=fixture();p[k]=v;self.assertEqual(m.Store().ingest(p)['state'],'held')
 def test_values(self):
  for k,v in [('distance_m',10**10000),('distance_m',True),('carried_weight_kg',float('nan')),('carried_weight_kg',float('inf')),('carried_weight_kg',None),('horse_id',1)]:
   p=fixture();p['rows'][0][k]=v;self.assertEqual(m.Store().ingest(p)['state'],'held')
 def test_available_still_unallocated(self):
  p=fixture();p['odds_status']='available';r=m.Store().ingest(p);self.assertFalse(r['allocation_allowed']);self.assertEqual(r['allocation_state'],'unallocated_not_implemented')
 def test_invalid_unicode(self):
  for key in ('source','race_key','horse_id'):
   for bad in ('x'+chr(0xd800),chr(0xdfff)):
    p=fixture()
    if key=='horse_id':p['rows'][0][key]=bad
    else:p[key]=bad
    a=m.Store();self.assertEqual(a.ingest(p)['state'],'held');self.assertEqual(a.rows(),[])
 def test_missing_distinct(self):
  p=fixture();p['odds_status']='missing';r=m.Store().ingest(p)
  self.assertEqual(r['allocation_state'],'unallocated_missing_odds');self.assertFalse(r['allocation_allowed'])
if __name__=='__main__':unittest.main()
