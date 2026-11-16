import unittest,copy
from datetime import datetime,timezone,timedelta
from history import extract
T=datetime(2140,1,2,tzinfo=timezone.utc)
def row(**kw):
 r=dict(race_key='old',horse_id='h',start=T-timedelta(days=1),available_at=T-timedelta(hours=23),observed_at=T-timedelta(hours=22),finish=2,field_size=12,source_ref='synthetic:fixture',source_hash='a'*64);r.update(kw);return r
def run(rows):return extract('h','target',T,T+timedelta(hours=1),rows)
class Tests(unittest.TestCase):
 def test_counts_missing_and_immutable(self):
  rows=[row(),row(race_key='old2',finish=None)];before=copy.deepcopy(rows);x=run(rows)
  self.assertEqual(x['top3_observed_fraction'],{'numerator':1,'denominator':1});self.assertEqual(x['missing_finish_runs'],1);self.assertIsNone(x['predicted_probability']);self.assertEqual(rows,before)
 def test_cutoff(self):
  x=run([row(observed_at=T+timedelta(seconds=1)),row(race_key='target')]);self.assertEqual(x['runs'],0);self.assertEqual(len(x['excluded']),2)
 def test_duplicate_and_conflict(self):
  self.assertEqual(run([row(),row()])['runs'],1)
  with self.assertRaises(ValueError):run([row(),row(finish=1)])
 def test_unknown_time_and_invalid_number(self):
  for kw in ({'available_at':None},{'finish':True},{'finish':13},{'source_hash':''}):
   with self.assertRaises(ValueError):run([row(**kw)])
 def test_order_hash_and_other_horse(self):
  a=row();b=row(race_key='old2',finish=5)
  self.assertEqual(run([a,b])['accepted_input_hash'],run([b,a])['accepted_input_hash']);self.assertEqual(run([row(horse_id='other')])['runs'],0)
if __name__=='__main__':unittest.main()
