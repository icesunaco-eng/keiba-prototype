import unittest,copy
from card_result_identity_audit import audit
class Tests(unittest.TestCase):
 def fixture(self):
  c=dict(source_sha256='a',race_holds=[],records=[dict(race_number=1,horse_number=1,horse_name_candidate='テスト',holds=[])])
  i=dict(event_date='2026-11-14',races=[dict(source_sha256='a',venue='東京')]);r=dict(schema='postrace-staging-v1',records=[dict(date='2026-11-14',venue='東京',race_no=1,umaban=1,name='テスト',source_sha256='b',finish=1,holds=[])])
  return c,i,r
 def test_agree_no_merge(self):
  args=self.fixture();old=copy.deepcopy(args);x=audit(*args);self.assertEqual(x['agreement_count'],1);self.assertFalse(x['result_fields_merged']);self.assertEqual(args,old);self.assertNotIn('finish',x['comparisons'][0])
 def test_different_event(self):
  c,i,r=self.fixture();r['records'][0]['date']='2026-11-15'
  with self.assertRaises(ValueError):audit(c,i,r)
 def test_difference(self):
  c,i,r=self.fixture();r['records'][0]['name']='別馬';self.assertEqual(audit(c,i,r)['comparisons'][0]['state'],'name_difference')
 def test_duplicate(self):
  c,i,r=self.fixture();r['records']*=2
  with self.assertRaises(ValueError):audit(c,i,r)
 def test_race_hold_propagates(self):
  c,i,r=self.fixture();c['race_holds']=[dict(race_number=1,reason='repeated_horse_content_conflict')]
  x=audit(c,i,r);self.assertEqual(x['agreement_count'],0);self.assertEqual(x['comparisons'][0]['state'],'held_source');self.assertEqual(x['comparisons'][0]['race_hold_reasons'],['repeated_horse_content_conflict'])
 def test_missing_race_holds_rejected(self):
  c,i,r=self.fixture();del c['race_holds']
  with self.assertRaisesRegex(ValueError,'race_holds_required'):audit(c,i,r)
if __name__=='__main__':unittest.main()
