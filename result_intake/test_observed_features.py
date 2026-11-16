import unittest,copy
import test_history_asof
from observed_features import extract
class Tests(unittest.TestCase):
 def runit(self,r=None,as_of='2026-11-17T00:00:00Z'):
  return extract(r or test_history_asof.Tests().fixture(),as_of=as_of,target_start='2026-11-21T01:00:00Z',target_race_ref='hypothetical')
 def test_counts_not_score(self):
  x=self.runit();h=x['horses'][0];self.assertEqual(h['facts']['wins'],1);self.assertTrue(all(g['score'] is None for g in h['groups'].values()));self.assertFalse(x['model_ready']);self.assertFalse(x['synthetic'])
 def test_future_observation_excluded(self):
  x=self.runit(as_of='2026-11-14T00:00:00Z');self.assertEqual(x['horses'][0]['facts']['observed_runs'],0)
 def test_missing_finish(self):
  r=test_history_asof.Tests().fixture();r[0]['rows'][0]['finish']=None;x=self.runit(r);self.assertEqual(x['horses'][0]['facts']['known_finish_runs'],0)
 def test_distance_course_counts(self):
  r=test_history_asof.Tests().fixture();r[0]['rows'][0].update(distance_m=1400,surface='ダ',venue='東京',going='良');x=self.runit(r)['horses'][0]['facts'];self.assertEqual(x['observed_distance_counts'],{'ダ:1400':1});self.assertEqual(x['observed_venue_counts'],{'東京':1})
 def test_nonmutation(self):
  r=test_history_asof.Tests().fixture();old=copy.deepcopy(r);self.runit(r);self.assertEqual(r,old)
if __name__=='__main__':unittest.main()
