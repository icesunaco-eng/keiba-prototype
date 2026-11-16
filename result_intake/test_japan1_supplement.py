import unittest,types
from japan1_supplement import install,MAPPINGS
class Tests(unittest.TestCase):
 def test_missing(self):
  original={1:'x'};m=types.SimpleNamespace(aj1_map=lambda:original)
  install(m);self.assertEqual(m.aj1_map()[7961],'芦');self.assertEqual(m.aj1_map()[8267],'辻');self.assertEqual(original,{1:'x'})
 def test_no_overwrite(self):
  m=types.SimpleNamespace(aj1_map=lambda:{7961:'?'});install(m)
  with self.assertRaisesRegex(ValueError,'conflicting'):m.aj1_map()
 def test_only_known(self):
  self.assertEqual(set(MAPPINGS),{7961,8267})
if __name__=='__main__':unittest.main()
