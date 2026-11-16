import unittest
from result_times import parse_time,compare
class Tests(unittest.TestCase):
 def test_fullwidth(self):self.assertEqual(parse_time('1：3 4．2')['seconds'],94.2)
 def test_ditto(self):self.assertIsNone(parse_time('〃')['seconds'])
 def test_invalid_seconds(self):self.assertEqual(parse_time('1:99.0')['status'],'hold')
 def test_unknown(self):self.assertEqual(parse_time('1:3�.0')['status'],'hold')
 def test_engine_disagreement(self):
  r=compare([dict(page=1,race_no=1,umaban=1,runner_status='unclassified',custom_time='1:34.2',pdfkit_time='1:34.3')])
  self.assertIsNone(r['checks'][0]['seconds']);self.assertFalse(r['model_ready'])
if __name__=='__main__':unittest.main()
from result_times import resolve_ditto
class ReferenceTests(unittest.TestCase):
 def rows(self):
  return [dict(page=1,race_no=1,umaban=i,runner_status='unclassified',custom_time=t,pdfkit_time=t,y=500-i) for i,t in [(1,'1:34.2'),(2,'〃'),(3,'〃')]]
 def test_chain(self):
  r=resolve_ditto(self.rows())['checks'];self.assertEqual([x['seconds'] for x in r],[94.2]*3);self.assertEqual(r[2]['time_reference'],[1,1,2]);self.assertTrue(all(x['finish'] is None and not x['dead_heat_inferred'] for x in r))
 def test_no_cross_race(self):
  r=self.rows();r[1]['race_no']=2;self.assertIsNone(resolve_ditto(r)['checks'][1]['seconds'])
 def test_break_on_unknown(self):
  r=self.rows();r[1]['custom_time']='?';self.assertIsNone(resolve_ditto(r)['checks'][2]['seconds'])
 def test_reject_bad_order(self):
  r=self.rows();r[1]['y']=510;self.assertIsNone(resolve_ditto(r)['checks'][1]['seconds'])
