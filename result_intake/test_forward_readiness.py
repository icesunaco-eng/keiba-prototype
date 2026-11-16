import unittest
from forward_readiness import checklist
class Tests(unittest.TestCase):
 def fixture(self,observed='2026-11-14T00:00:00Z'):
  c=dict(source_sha256='a',race_holds=[],listed_count=1,records=[dict(race_number=1,horse_number=1,horse_name_candidate='合成馬',holds=[],source_sha256='a',observed_at=observed)])
  i=dict(event_date='2026-11-14',races=[dict(race_number=1,source_sha256='a',listed_count=1,post_time_candidate='2026-11-14T09:50:00+09:00')]);return c,i
 def runit(self,args=None,**kw):
  params=dict(as_of='2026-11-14T00:30:00Z',target_start='2026-11-14T00:50:00Z',target_event_date='2026-11-14');params.update(kw);return checklist(*(args or self.fixture()),**params)
 def test_synthetic_prestart_still_requires_review(self):
  x=self.runit();self.assertNotIn('card_acquired_after_its_race_start',x['gap_counts']);self.assertFalse(x['rows'][0]['ready']);self.assertIn('stable_horse_id_unresolved',x['gap_counts'])
 def test_historical_never_forward(self):
  x=self.runit(self.fixture('2026-11-16T06:00:00Z'),as_of='2026-11-17T00:00:00Z',target_start='2026-11-21T01:00:00Z',target_event_date='2026-11-21');self.assertEqual(x['gap_counts']['card_event_is_not_target_event'],1);self.assertEqual(x['gap_counts']['card_acquired_after_its_race_start'],1)
 def test_late_observation(self):self.assertIn('card_observed_after_cutoff',self.runit(self.fixture('2026-11-14T00:40:00Z'))['gap_counts'])
 def test_missing_horse(self):
  c,i=self.fixture();c['records']=[]
  with self.assertRaises(ValueError):self.runit((c,i))
if __name__=='__main__':unittest.main()
