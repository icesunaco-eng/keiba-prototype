import unittest,copy
from result_dataset import build
class Tests(unittest.TestCase):
 def fixture(self):
  r=[dict(source_sha256='a'*64,page=1,race_no=1,date='2026-11-14',venue='東京',listed_count=1,excluded_count=0,field_size=1)]
  k=dict(page=1,race_no=1,umaban=1)
  def e(row):return dict(source_sha256='a'*64,source_kind='post_race_results',rows=[dict(k,**row)])
  return [r,e(dict(name='架空馬',status='two_engine_agreement')),e(dict(seconds=90.1,status='explicit_time')),e(dict(win_odds=2.5,status='two_engine_agreement',umaban_check='two_engine_agreement',table_position=1))]
 def test_join(self):
  r=build(*self.fixture());self.assertEqual(r['race_summary'][0]['starters'],1);self.assertIsNone(r['records'][0]['finish']);self.assertFalse(r['model_ready'])
 def test_hash(self):
  f=self.fixture();f[2]['source_sha256']='b'*64
  with self.assertRaises(ValueError):build(*f)
 def test_duplicate(self):
  f=self.fixture();f[1]['rows']*=2
  with self.assertRaises(ValueError):build(*f)
 def test_missing(self):
  f=self.fixture();f[2]['rows']=[]
  with self.assertRaises(ValueError):build(*f)
 def test_prerace(self):
  f=self.fixture();f[1]['source_kind']='pre_race'
  with self.assertRaises(ValueError):build(*f)
 def test_count(self):
  f=self.fixture();f[0][0]['field_size']=2
  with self.assertRaises(ValueError):build(*f)
 def test_nonfinite(self):
  f=self.fixture();f[2]['rows'][0]['seconds']=float('inf')
  with self.assertRaises(ValueError):build(*f)
 def test_hold(self):
  f=self.fixture();f[1]['rows'][0]['status']='hold'
  self.assertEqual(build(*f)['records'][0]['holds'],['name'])
 def test_no_mutate(self):
  f=self.fixture();old=copy.deepcopy(f);build(*f);self.assertEqual(f,old)
 def chain(self):
  f=self.fixture();f[0][0].update(listed_count=3,field_size=3)
  for envelope in f[1:]:
   original=envelope['rows'][0]
   envelope['rows']=[dict(original,umaban=i) for i in (1,2,3)]
  for i,row in enumerate(f[3]['rows'],1):row['table_position']=i
  for i in (1,2):f[2]['rows'][i].update(status='referenced_time',time_reference=[1,1,i])
  return f
 def test_reference_chain(self):
  result=build(*self.chain());self.assertTrue(all(not r['holds'] for r in result['records']))
 def test_reference_bad_target(self):
  for target in (None,[],[99,12,18],[1,1,2],[1,2,1],[1,1,True]):
   f=self.chain();f[2]['rows'][1]['time_reference']=target
   with self.subTest(target=target),self.assertRaises(ValueError):build(*f)
 def test_reference_cycle(self):
  f=self.chain();f[2]['rows'][0].update(status='referenced_time',time_reference=[1,1,3])
  with self.assertRaises(ValueError):build(*f)
 def test_reference_seconds(self):
  for seconds in (90.2,None,float('inf'),float('nan'),True):
   f=self.chain();f[2]['rows'][1]['seconds']=seconds
   with self.subTest(seconds=seconds),self.assertRaises(ValueError):build(*f)
 def test_reference_held_source(self):
  f=self.chain();f[2]['rows'][0]['status']='hold'
  with self.assertRaises(ValueError):build(*f)
 def test_reference_skip(self):
  f=self.chain();f[2]['rows'][2]['time_reference']=[1,1,1]
  with self.assertRaises(ValueError):build(*f)
 def test_reference_duplicate_position(self):
  f=self.chain();f[3]['rows'][2]['table_position']=1
  with self.assertRaises(ValueError):build(*f)
 def test_unexpected_reference(self):
  f=self.fixture();f[2]['rows'][0]['time_reference']=[1,1,1]
  with self.assertRaises(ValueError):build(*f)
if __name__=='__main__':unittest.main()
