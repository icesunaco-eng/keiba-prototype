import unittest
from source_observation import attach
class Tests(unittest.TestCase):
 def test_wrong_source(self):
  with self.assertRaises(ValueError):attach({},b'bad',b'',b'')
 def test_wrong_type(self):
  with self.assertRaises(ValueError):attach({},'bad',b'',b'')
if __name__=='__main__':unittest.main()
