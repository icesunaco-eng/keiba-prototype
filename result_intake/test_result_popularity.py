import unittest
from result_popularity import validate
class Tests(unittest.TestCase):
 def fixture(self):
  return dict(source_kind='post_race_results',records=[dict(page=1,race_no=1,umaban=i,excluded=False,popularity_candidate=i,win_odds=float(i),holds=[],status='verified_columns_only') for i in (1,2,3)])
 def test_ok(self):
  x=validate(self.fixture());self.assertEqual(x['race_summary'][0]['status'],'race_consistency_passed');self.assertFalse(x['rows'][0]['popularity_verified']);self.assertFalse(x['model_ready'])
 def test_tied_rounded_odds_not_tied_popularity(self):
  f=self.fixture();f['records'][1]['win_odds']=1.0
  self.assertEqual(validate(f)['race_summary'][0]['status'],'race_consistency_passed')
 def test_invalid_popularities(self):
  for v in (None,True,1,4,'2'):
   f=self.fixture();f['records'][1]['popularity_candidate']=v
   with self.subTest(v=v):self.assertEqual(validate(f)['race_summary'][0]['status'],'hold')
 def test_inversion(self):
  f=self.fixture();f['records'][0]['win_odds']=10.;self.assertEqual(validate(f)['race_summary'][0]['reasons'],['odds_popularity_inversion'])
 def test_excluded(self):
  f=self.fixture();f['records'].append(dict(page=1,race_no=1,umaban=4,excluded=True,popularity_candidate=None,win_odds=None,holds=[],status='verified_columns_only'));self.assertEqual(validate(f)['race_summary'][0]['starters'],3)
 def test_hold(self):
  f=self.fixture();f['records'][0]['holds']=['name'];self.assertIn('upstream_hold',validate(f)['race_summary'][0]['reasons'])
 def test_duplicate(self):
  f=self.fixture();f['records'].append(f['records'][0])
  with self.assertRaises(ValueError):validate(f)
 def test_prerace(self):
  f=self.fixture();f['source_kind']='pre_race'
  with self.assertRaises(ValueError):validate(f)
if __name__=='__main__':unittest.main()
