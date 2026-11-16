import unittest,hashlib
from profile_history import parse,batch,horse_key
class Tests(unittest.TestCase):
 def fixture(self):
  cells=['2026年10月10日','東京','<a href="/JRADB/accessS.html?CNAME=fixture">未勝利</a>','ダ1300','良','16','6','3','騎手','55.0','440','1:20.2','','馬']
  raw=('出走レース 頭数 着順 負担重量 <tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr>').encode()
  return raw,dict(sha256=hashlib.sha256(raw).hexdigest(),acquired_at='2026-11-16T06:00:00Z',url='https://www.jra.go.jp/JRADB/accessU.html?CNAME=pw01dud102024106071%2F52')
 def test_parse(self):
  x=parse(*self.fixture());self.assertEqual(x['rows'][0]['finish'],3);self.assertIsNone(x['rows'][0]['start_at']);self.assertFalse(x['model_ready'])
 def test_key_variants(self):
  self.assertEqual(horse_key(self.fixture()[1]['url']),horse_key('https://sp.jra.jp/JRADB/accessU.html?CNAME=sj01ude002024106071/E0'))
 def test_bad_domain(self):
  with self.assertRaises(ValueError):horse_key('https://evil.test/JRADB/accessU.html?CNAME=pw01dud102024106071/52')
 def test_hash(self):
  r,m=self.fixture()
  with self.assertRaises(ValueError):parse(r+b'bad',m)
 def test_naive(self):
  r,m=self.fixture();m['acquired_at']='2026-11-16T06:00:00'
  with self.assertRaises(ValueError):parse(r,m)
 def test_duplicate(self):
  with self.assertRaises(ValueError):batch([self.fixture(),self.fixture()])
 def test_birth_date_not_history(self):
  r,m=self.fixture();r+='<tr><td>2024年2月7日</td></tr>'.encode();m['sha256']=hashlib.sha256(r).hexdigest();self.assertEqual(len(parse(r,m)['rows']),1)
 def test_mobile(self):
  _,m=self.fixture()
  r=('出走レース 頭数 着順 負担重量 <tr><td>2026年10月10日 東京<br><a href="/JRADB/accessS.html?CNAME=fixture">未勝利</a><br>ダ1300 良 16頭 馬</td><td>3着 6人気<br>1:20.2<br>440kg<br>騎手 55.0</td></tr>').encode()
  m['sha256']=hashlib.sha256(r).hexdigest();self.assertEqual(parse(r,m)['rows'][0]['finish'],3)
if __name__=='__main__':unittest.main()
