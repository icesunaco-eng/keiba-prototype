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
