import unittest,hashlib
import test_profile_identity
from card_profile_links import extract
class Tests(unittest.TestCase):
 def test_extract(self):
  c,m,_,_=test_profile_identity.Tests().fixture();x=extract(c,m);self.assertEqual(x['records'][0]['horse_key'],'jra-profile:2024106071');self.assertFalse(x['profile_pages_fetched'])
 def test_duplicate(self):
  c,m,_,_=test_profile_identity.Tests().fixture();c+=c;m['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):extract(c,m)
 def test_gap(self):
  c,m,_,_=test_profile_identity.Tests().fixture();c=c.replace(b'>1</td>',b'>2</td>');m['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):extract(c,m)
if __name__=='__main__':unittest.main()
