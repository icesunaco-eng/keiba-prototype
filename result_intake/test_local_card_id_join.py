import unittest
from local_card_id_join import join
class Tests(unittest.TestCase):
 def fixture(self):
  c=dict(source_sha256='pdf',listed_count=1,race_holds=[],records=[dict(race_number=1,horse_number=1,horse_name_candidate='馬',holds=[],observed_at='after')]);i=dict(event_date='2026-11-14',races=[dict(race_number=1,venue='東京',meeting=5,meeting_day=3,listed_count=1,source_sha256='pdf')]);l=dict(source_url='https://sp.jra.jp/JRADB/accessD.html?CNAME=sw01ddd0105202605030120261114/B3',source_sha256='html',observed_at='after',records=[dict(horse_number=1,horse_name='馬',horse_key='id')]);return c,i,l
 def test_scoped(self):self.assertEqual(join(*self.fixture())['linked_count'],1)
 def test_date_conflict(self):
  c,i,l=self.fixture();i['event_date']='2026-11-15'
  with self.assertRaises(ValueError):join(c,i,l)
 def test_name_conflict(self):
  c,i,l=self.fixture();l['records'][0]['horse_name']='別'
  with self.assertRaises(ValueError):join(c,i,l)
if __name__=='__main__':unittest.main()
