import unittest
import test_card_pdf_inventory
from card_pdf_horses import extract
class Tests(unittest.TestCase):
 def fixture(self):
  p,t,m=test_card_pdf_inventory.Tests().fixture();t=t.replace('14頭','1頭')+'白 1\n牝2\nF2\n栗55\n（2020年）0\nテスト 横山 太郎\nTest (JPN)\n2024．2．7生\n';return p,t,m
 def test_candidate(self):
  x=extract(*self.fixture(),expected_races=1);self.assertFalse(x['race_holds']);self.assertEqual(x['records'][0]['horse_name_candidate'],'テスト');self.assertFalse(x['model_ready'])
 def test_coverage(self):
  p,t,m=self.fixture();x=extract(p,t.replace('1頭','2頭'),m,expected_races=1);self.assertEqual(x['race_holds'][0]['reason'],'horse_number_coverage')
if __name__=='__main__':unittest.main()
