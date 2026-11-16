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
if __name__=='__main__':unittest.main()
