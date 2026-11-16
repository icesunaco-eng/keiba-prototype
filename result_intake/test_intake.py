import unittest,copy
from intake import stage
R={'date':'2140-01-01','venue':'架空場','kai':1,'nichi':1,'race_no':1,'field_size':2,'rows':[{'umaban':2,'name':'架空B','order_by_row':1},{'umaban':1,'name':'架空A','order_by_row':2}],'announced_at':None,'post_label':'09:50','issues':[],'holds':[]}
M={'url':'https://example.invalid/fixture','sha256':'a'*64,'fetched_utc':'2140-01-02T01:00:00Z'}
class Tests(unittest.TestCase):
 def test_stage_not_predict(self):
  r=copy.deepcopy(R);x=stage(r,M);self.assertEqual(x['state'],'staged_not_model_input');self.assertEqual(x['history_rows'],[]);self.assertTrue(all(e['horse_id'] is None for e in x['entries']));self.assertEqual(r,R);self.assertIsNone(x['announced_at'])
 def test_duplicate_and_hold(self):
  for change in ({'holds':['missing']},{'field_size':3},{'rows':[R['rows'][0]]*2}):
   with self.assertRaises(ValueError):stage(dict(R,**change),M)
 def test_provenance(self):
  for change in ({'sha256':''},{'fetched_utc':'2140-01-02T01:00:00'},{'fetched_utc':'2139-01-02T01:00:00Z'}):
   with self.assertRaises(ValueError):stage(R,dict(M,**change))
 def test_no_odds_passthrough(self):
  r=copy.deepcopy(R);r['rows'][0]['odds']=123;r['payouts']={'x':999};x=stage(r,M)
  self.assertNotIn('odds',str(x));self.assertNotIn('payouts',str(x))
if __name__=='__main__':unittest.main()
