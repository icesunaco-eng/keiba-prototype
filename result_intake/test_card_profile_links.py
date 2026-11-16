import unittest,hashlib
import test_profile_identity
from card_profile_links import extract
class Tests(unittest.TestCase):
 def test_extract(self):
  c,m,_,_=test_profile_identity.Tests().fixture();x=extract(c,m,expected_count=1);self.assertEqual(x['records'][0]['horse_key'],'jra-profile:2024106071');self.assertFalse(x['profile_pages_fetched'])
 def test_duplicate(self):
  c,m,_,_=test_profile_identity.Tests().fixture();c+=c;m['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):extract(c,m,expected_count=1)
 def test_gap(self):
  c,m,_,_=test_profile_identity.Tests().fixture();c=c.replace(b'>1</td>',b'>2</td>');m['sha256']=hashlib.sha256(c).hexdigest()
  with self.assertRaises(ValueError):extract(c,m,expected_count=1)
 def test_truncated_tail(self):
  c,m,_,_=test_profile_identity.Tests().fixture()
  with self.assertRaisesRegex(ValueError,'card_number_coverage'):extract(c,m,expected_count=2)
 def test_invalid_expectation(self):
  c,m,_,_=test_profile_identity.Tests().fixture()
  with self.assertRaisesRegex(ValueError,'expected_count_required'):extract(c,m,expected_count=True)
if __name__=='__main__':unittest.main()
