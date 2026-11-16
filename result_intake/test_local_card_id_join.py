import unittest
from local_card_id_join import join
class Tests(unittest.TestCase):
 def fixture(self):
  c=dict(source_sha256='pdf',listed_count=1,race_holds=[],records=[dict(race_number=1,horse_number=1,horse_name_candidate='馬',holds=[],source_sha256='pdf',observed_at='2026-11-16T06:00:00Z')]);i=dict(event_date='2026-11-14',races=[dict(race_number=1,venue='東京',meeting=5,meeting_day=3,listed_count=1,source_sha256='pdf',observed_at='2026-11-16T06:00:00Z')]);l=dict(source_url='https://sp.jra.jp/JRADB/accessD.html?CNAME=sw01ddd0105202605030120261114/B3',source_sha256='html',observed_at='2026-11-16T06:00:00Z',records=[dict(horse_number=1,horse_name='馬',horse_key='jra-profile:2024106071',profile_url='https://sp.jra.jp/JRADB/accessU.html?CNAME=sj01ude002024106071/E0',card_source_sha256='html',observed_at='2026-11-16T06:00:00Z')]);return c,i,l
 def test_scoped(self):self.assertEqual(join(*self.fixture())['linked_count'],1)
 def test_date_conflict(self):
  c,i,l=self.fixture();i['event_date']='2026-11-15'
  with self.assertRaises(ValueError):join(c,i,l)
 def test_name_conflict(self):
  c,i,l=self.fixture();l['records'][0]['horse_name']='別'
  with self.assertRaises(ValueError):join(c,i,l)
 def test_external_domain(self):
  c,i,l=self.fixture();l['source_url']=l['source_url'].replace('sp.jra.jp','evil.example')
  with self.assertRaisesRegex(ValueError,'official_card_url'):join(c,i,l)
 def test_html_provenance(self):
  c,i,l=self.fixture();l['records'][0]['card_source_sha256']='other'
  with self.assertRaisesRegex(ValueError,'html_row_provenance'):join(c,i,l)
 def test_pdf_provenance(self):
  c,i,l=self.fixture();c['records'][0]['source_sha256']='other'
  with self.assertRaisesRegex(ValueError,'pdf_row_provenance'):join(c,i,l)
 def test_duplicate_profile_key(self):
  c,i,l=self.fixture();l['records'].append(dict(l['records'][0],horse_number=2))
  with self.assertRaisesRegex(ValueError,'profile_key_integrity'):join(c,i,l)
 def test_link_key_mismatch(self):
  c,i,l=self.fixture();l['records'][0]['horse_key']='different'
  with self.assertRaisesRegex(ValueError,'profile_key_integrity'):join(c,i,l)
if __name__=='__main__':unittest.main()
