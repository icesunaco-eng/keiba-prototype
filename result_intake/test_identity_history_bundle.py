import unittest,copy,hashlib
import test_profile_identity
from identity_history_bundle import build
class Tests(unittest.TestCase):
 def runit(self,cutoff='2026-11-17T00:00:00Z',card_observed=None):
  c,cm,p,pm=test_profile_identity.Tests().fixture()
  if card_observed:cm['acquired_at']=card_observed
  return build(c,cm,[(p,pm)],as_of=cutoff,target_start='2026-11-21T01:00:00Z',target_race_ref='synthetic')
 def test_end_to_end(self):
  x=self.runit();self.assertEqual(x['identity_matches'],1);self.assertEqual(len(x['candidates']),1);self.assertEqual(x['candidates'][0]['identity_evidence']['card_number'],1);self.assertFalse(x['model_ready']);self.assertEqual(len(x['holds']),2)
 def test_late_card(self):
  x=self.runit(card_observed='2026-11-18T00:00:00Z');self.assertFalse(x['candidates']);self.assertEqual(len(x['held_identity']),1)
 def test_past_cutoff(self):
  x=self.runit('2026-11-14T00:00:00Z');self.assertFalse(x['candidates']);self.assertEqual(len(x['excluded_history']),1)
 def test_tamper_snapshot(self):
  c,cm,p,pm=test_profile_identity.Tests().fixture()
  with self.assertRaises(ValueError):build(c,cm,[(p+b'x',pm)],as_of='2026-11-17T00:00:00Z',target_start='2026-11-21T01:00:00Z',target_race_ref='synthetic')
 def test_immutable(self):
  c,cm,p,pm=test_profile_identity.Tests().fixture();before=copy.deepcopy((cm,pm))
  build(c,cm,[(p,pm)],as_of='2026-11-17T00:00:00Z',target_start='2026-11-21T01:00:00Z',target_race_ref='synthetic');self.assertEqual((cm,pm),before)
if __name__=='__main__':unittest.main()
