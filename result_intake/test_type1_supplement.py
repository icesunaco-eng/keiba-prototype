import unittest,types,hashlib,re
from type1_supplement import install
class Tests(unittest.TestCase):
 def module(self):
  def ref(b,k):
   x=re.search(rb'/'+k+rb'\s+(\d+) 0 R',b);return int(x[1]) if x else None
  return types.SimpleNamespace(ref=ref,stream_of=lambda b:b,page_fonts=lambda o,p:{'F':({2:'{c033}',3:'{c034}'},False)})
 def test_exact_hash_only(self):
  objs={1:b'/FontDescriptor 2 0 R',2:b'/FontFile3 3 0 R',3:b'font'}
  m=self.module();install(m,{hashlib.sha256(b'font').hexdigest():{'{c033}':'①'}})
  r=m.page_fonts(objs,b'/Font<</F 1 0 R>>')
  self.assertEqual(r['F'][0],{2:'①',3:'{c034}'})
  objs[3]=b'different';self.assertEqual(m.page_fonts(objs,b'/Font<</F 1 0 R>>')['F'][0][2],'{c033}')
 def test_no_registry_no_change(self):
  m=self.module();install(m,{})
  self.assertEqual(m.page_fonts({},b'')['F'][0][2],'{c033}')
if __name__=='__main__':unittest.main()
