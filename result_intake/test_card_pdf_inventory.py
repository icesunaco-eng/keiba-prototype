import unittest,hashlib
from card_pdf_inventory import inventory
class Tests(unittest.TestCase):
 def fixture(self):
  p=b'%PDF-synthetic';t='2026年5東京3 TEST\n1，400" （14頭）\n1R\nTime 9時50分\n'
  m=dict(sha256=hashlib.sha256(p).hexdigest(),url='https://www.jra.go.jp/keiba/rpdf/pdf/20261114-05tokyo03.pdf',acquired_at='2026-11-16T06:00:00Z');return p,t,m
 def test_header(self):
  x=inventory(*self.fixture(),expected_races=1);self.assertEqual(x['total_listed'],14);self.assertFalse(x['model_ready'])
 def test_duplicate(self):
  p,t,m=self.fixture()
  with self.assertRaises(ValueError):inventory(p,t+t,m,expected_races=2)
 def test_missing_time(self):
  p,t,m=self.fixture()
  with self.assertRaises(ValueError):inventory(p,t.replace('Time','Unknown'),m,expected_races=1)
 def test_hash(self):
  p,t,m=self.fixture()
  with self.assertRaises(ValueError):inventory(p+b'x',t,m,expected_races=1)
 def test_repeat_evidence_preserved(self):
  p,t,m=self.fixture();x=inventory(p,t+t,m,expected_races=1);self.assertEqual(x['total_listed'],14);self.assertEqual(len(x['races'][0]['repeated_header_spans']),2)
 def test_repeat_conflict(self):
  p,t,m=self.fixture()
  with self.assertRaisesRegex(ValueError,'conflicting_repeated_header'):inventory(p,t+t.replace('14頭','15頭'),m,expected_races=1)
if __name__=='__main__':unittest.main()
