import unittest,tempfile,json,hashlib
from pathlib import Path
from load_input import read_json,summarize
from test_intake import R,M
class Tests(unittest.TestCase):
 def test_original_float_and_raw_hash(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.json';raw=b'{"number":7.0}';p.write_bytes(raw)
   x,h=read_json(p);self.assertIs(type(x['number']),float);self.assertEqual(h,hashlib.sha256(raw).hexdigest());self.assertEqual(p.read_bytes(),raw)
 def test_bad_json_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x'
   for s in ('{"x":1,"x":2}','{"x":NaN}','[]'):
    p.write_text(s)
    with self.assertRaises(ValueError):read_json(p)
 def test_summary_no_source_values(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'r';m=Path(d)/'m';p.write_text(json.dumps(R));m.write_text(json.dumps(M));before=(p.read_bytes(),m.read_bytes())
   x=summarize(p,m);self.assertEqual(x['entries'],2);self.assertFalse(x['model_ready']);self.assertNotIn('架空A',str(x));self.assertNotIn('https://',str(x));self.assertEqual((p.read_bytes(),m.read_bytes()),before)
if __name__=='__main__':unittest.main()
