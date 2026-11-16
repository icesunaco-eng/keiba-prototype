import unittest,hashlib
import test_profile_history
from profile_identity import match
class Tests(unittest.TestCase):
 def fixture(self):
  p,pm=test_profile_history.Tests().fixture();p=b'<h1>'+ '競走馬情報テスト'.encode()+b'</h1>'+p;pm['sha256']=hashlib.sha256(p).hexdigest()
  c=('<tr><td class="num">1</td><td><a href="/JRADB/accessU.html?CNAME=sj01ude002024106071/E0">テスト</a></td></tr>').encode()
  cm=dict(url='https://sp.jra.jp/JRADB/accessD.html?CNAME=fixture',sha256=hashlib.sha256(c).hexdigest(),acquired_at='2026-11-16T06:00:00Z')
  return c,cm,p,pm
 def test_link_and_name(self):self.assertEqual(match(*self.fixture())['status'],'saved_card_profile_identity_matched')
 def test_same_name_different_key(self):
  c,cm,p,pm=self.fixture();c=c.replace(b'2024106071',b'2024106072');cm['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):match(c,cm,p,pm)
 def test_key_matches_name_differs(self):
  c,cm,p,pm=self.fixture();c=c.replace('テスト'.encode(),'別馬'.encode());cm['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):match(c,cm,p,pm)
 def test_duplicate_link(self):
  c,cm,p,pm=self.fixture();c+=c;cm['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):match(c,cm,p,pm)
 def test_hash(self):
  c,cm,p,pm=self.fixture()
  with self.assertRaises(ValueError):match(c+b'x',cm,p,pm)
if __name__=='__main__':unittest.main()
