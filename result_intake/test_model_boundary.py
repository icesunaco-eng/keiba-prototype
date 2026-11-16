import unittest
from model_boundary import assess
class Tests(unittest.TestCase):
 def test_real_held(self):
  x=assess(dict(schema='identity-history-staging-v1',model_ready=False,production_ready=False,candidates=[{}]*9,identity_matches=2))
  self.assertEqual(x['conditional_history_rows'],9);self.assertFalse(x['synthetic_flag_added']);self.assertFalse(x['prediction_executed'])
 def test_promotion_rejected(self):
  with self.assertRaises(ValueError):assess(dict(schema='identity-history-staging-v1',model_ready=True,production_ready=False))
if __name__=='__main__':unittest.main()
