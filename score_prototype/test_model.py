import importlib.util,pathlib,unittest,copy,math
from datetime import datetime,timezone,timedelta
spec=importlib.util.spec_from_file_location('model',pathlib.Path(__file__).with_name('model.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
T=datetime(2140,4,11,10,tzinfo=timezone.utc)
def fixture(equal=False):
 return {'synthetic':True,'as_of':T,'scheduled_start':T+timedelta(minutes=15),'horses':[{'id':str(i),'features':{g:{'value':5 if equal else 9-i,'observed_at':T,'available_at':T,'source':'fabricated-test-only','target_outcome':False} for g in m.GROUPS}} for i in range(1,5)]}
class Test(unittest.TestCase):
 def test_normal(self):
  r=m.predict(fixture());self.assertEqual(r['state'],'synthetic_rule_prototype');self.assertEqual(r['trio_candidates'][0]['horses'],['1','2','3']);self.assertAlmostEqual(sum(r['model_win_probabilities'].values()),1);self.assertAlmostEqual(sum(c['model_probability'] for c in r['trio_candidates']),1);self.assertFalse(r['official_performance_eligible']);self.assertFalse(r['order_created'])
 def test_equal(self):
  r=m.predict(fixture(True));self.assertTrue(all(abs(v-.25)<1e-12 for v in r['model_win_probabilities'].values()));self.assertTrue(all(abs(c['model_probability']-.25)<1e-12 for c in r['trio_candidates']))
 def test_three(self):
  q=fixture();q['horses'].pop();self.assertAlmostEqual(m.predict(q)['trio_candidates'][0]['model_probability'],1)
 def test_fail_closed(self):
  changes=[lambda q:q.update(synthetic=False),lambda q:q.update(as_of=None),lambda q:q.update(scheduled_start=T),lambda q:q.update(payout=100),lambda q:q['horses'][0]['features'].pop('pace'),lambda q:q['horses'][0].update(id='2')]
  for change in changes:
   q=fixture();change(q);self.assertEqual(m.predict(q)['state'],'held')
 def test_feature_boundaries(self):
  for key,value in [('value',None),('value',True),('value',float('nan')),('value',11),('target_outcome',0),('target_outcome',True),('source',' '),('observed_at',None),('available_at',T+timedelta(microseconds=1)),('observed_at',T+timedelta(microseconds=1)),('payout',100)]:
   q=fixture();q['horses'][0]['features']['ability'][key]=value;self.assertEqual(m.predict(q)['state'],'held',(key,value))
 def test_extreme_and_invalid_numbers(self):
  for value in (10**10000,-10**10000,float('inf'),-float('inf'),'5',[],{},complex(5,0)):
   q=fixture();q['horses'][0]['features']['ability']['value']=value
   self.assertEqual(m.predict(q)['state'],'held')
if __name__=='__main__':unittest.main()
