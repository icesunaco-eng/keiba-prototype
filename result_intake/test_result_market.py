import unittest
from result_market import parse_market,compare
class Tests(unittest.TestCase):
 def row(self):return dict(page=1,race_no=1,umaban=2,runner_status='unclassified',table_position=1,custom_market='2．8①',pdfkit_market='2.8 ①',pdfkit_umaban='2')
 def test_parse(self):self.assertEqual(parse_market('2．8①'),dict(win_odds=2.8,popularity=1))
 def test_unknown(self):self.assertIsNone(parse_market('2．8{c033}'))
 def test_agree(self):
  r=compare([self.row()]);self.assertEqual(r['checks'][0]['popularity_candidate'],1);self.assertFalse(r['model_ready']);self.assertIsNone(r['checks'][0]['finish'])
 def test_disagree(self):
  a=self.row();a['pdfkit_market']='2.9①';self.assertEqual(compare([a])['checks'][0]['status'],'hold')
 def test_number_mismatch(self):
  a=self.row();a['pdfkit_umaban']='3';self.assertEqual(compare([a])['checks'][0]['umaban_check'],'hold')
if __name__=='__main__':unittest.main()

class OpaqueTests(unittest.TestCase):
 def test_opaque_popularity_not_promoted(self):
  a=Tests().row();a['pdfkit_market']='2.8!'
  r=compare([a])['checks'][0]
  self.assertEqual(r['win_odds'],2.8);self.assertEqual(r['popularity_check'],'glyph_mapping_only')
