import unittest,copy
from history_asof import select
class Tests(unittest.TestCase):
 def fixture(self):
  r=dict(horse_key='horse1',source_kind='post_race_history_snapshot',observed_at='2026-11-16T06:00:00Z',rows=[])
  r['rows']=[dict(horse_key='horse1',source_kind=r['source_kind'],observed_at=r['observed_at'],race_ref='result1',event_date='2026-11-14',finish=1,field_size=14,source_sha256='a'*64,source_url='source',published_at=None,start_at=None)]
  return [r]
 def runit(self,r=None,**kw):
  args=dict(as_of='2026-11-17T00:00:00Z',target_start='2026-11-21T01:00:00Z',target_race_ref='future1');args.update(kw)
  return select(r or self.fixture(),**args)
 def test_conditional(self):
  x=self.runit();self.assertEqual(len(x['candidates']),1);self.assertEqual(len(x['holds']),2);self.assertFalse(x['model_ready']);self.assertIsNone(x['candidates'][0]['published_at'])
 def test_historical_cutoff(self):
  x=self.runit(as_of='2026-11-14T00:00:00Z');self.assertEqual(x['candidates'],[]);self.assertIn('snapshot_not_observed_by_cutoff',x['excluded'][0]['reasons'])
 def test_target_itself(self):self.assertFalse(self.runit(target_race_ref='result1')['candidates'])
 def test_same_day(self):
  r=self.fixture();r[0]['rows'][0]['event_date']='2026-11-16'
  self.assertFalse(self.runit(r,as_of='2026-11-16T07:00:00Z')['candidates'])
 def test_exact_observation(self):self.assertEqual(len(self.runit(as_of='2026-11-16T06:00:00Z')['candidates']),1)
 def test_prestart(self):
  with self.assertRaises(ValueError):self.runit(as_of='2026-11-22T00:00:00Z')
 def test_naive(self):
  with self.assertRaises(ValueError):self.runit(as_of='2026-11-17T00:00:00')
 def test_snapshot_mismatch(self):
  r=self.fixture();r[0]['rows'][0]['observed_at']='2026-11-13T00:00:00Z'
  with self.assertRaises(ValueError):self.runit(r)
 def test_duplicate(self):
  r=self.fixture();r.append(copy.deepcopy(r[0]))
  with self.assertRaises(ValueError):self.runit(r)
 def test_immutability(self):
  r=self.fixture();b=copy.deepcopy(r);self.runit(r);self.assertEqual(r,b)
 def test_nonnull_original_timing_rejected(self):
  for field in ('published_at','start_at'):
   for value in ('2026-11-30T00:00:00Z','2026-11-13T00:00:00Z','bad',0):
    with self.subTest(field=field,value=value):
     r=self.fixture();r[0]['rows'][0][field]=value
     with self.assertRaisesRegex(ValueError,'unsupported_original_timing'):self.runit(r)
if __name__=='__main__':unittest.main()
