import unittest
from classify_glyphs import classify_page
import test_type1_supplement as fixture
import hashlib
class Tests(unittest.TestCase):
 def test_class_and_keep(self):
  m=fixture.Tests().module();o={1:b'/FontDescriptor 2 0 R',2:b'/FontFile3 3 0 R',3:b'font'};p=[dict(f='F',t='{c033}{c034}',x=1,y=2)]
  reg={hashlib.sha256(b'font').hexdigest():{'{c033}':'course_diagram'}}
  r=classify_page(m,o,b'/Font<</F 1 0 R>>',p,reg)
  self.assertEqual([x['category'] for x in r],['course_diagram','unclassified']);self.assertEqual(p[0]['t'],'{c033}{c034}')
if __name__=='__main__':unittest.main()
