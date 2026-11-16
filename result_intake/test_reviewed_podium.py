import json,unittest,copy
from pathlib import Path
from reviewed_podium import join,SOURCE_SHA256,PODIUM
class Tests(unittest.TestCase):
 def fixture(self):
  # Synthetic full-count fixture contains no private source data.
  counts=[14,18,14,12,10,15,16,13,14,17,16,12];rows=[];summary=[];reports=[]
  for race,count in enumerate(counts,1):
   page=(race+1)//2;pod=PODIUM[race];nums=pod+[n for n in range(1,count+1) if n not in pod]
   # Source 12R has umaban 11 excluded; all other fields are synthetic.
   for pos,num in enumerate(nums,1):
    excluded=race==12 and num==11
    rows.append(dict(page=page,race_no=race,umaban=num,date='2026-11-14',venue='東京',source_sha256=SOURCE_SHA256,excluded=excluded,holds=[],status='verified_columns_only',table_position=pos,finish=None,popularity_candidate=None if excluded else pos,win_odds=None if excluded else float(pos)))
   summary.append(dict(page=page,race_no=race,listed=count,excluded=int(race==12),starters=count-int(race==12)))
   reports.append(dict(page=page,race_no=race,source_sha256=SOURCE_SHA256,status='payout_and_table_agreement',podium_candidate=pod,reasons=[],evidence={label:dict(numbers=pod[:n]) for label,n in [('単勝',1),('馬単',2),('3連単',3)]}))
  return [dict(records=rows,race_summary=summary,source_kind='post_race_results'),dict(races=reports,source_kind='post_race_results')]
 def test_exact_scope(self):
  x=join(*self.fixture());self.assertEqual(sum(r['finish'] is not None for r in x['records']),36);self.assertFalse(x['model_ready']);self.assertIsNone(x['provenance']['acquired_at']);self.assertFalse(x['provenance']['prediction_input_allowed'])
 def test_hash(self):
  f=self.fixture();f[0]['records'][0]['source_sha256']='a'*64
  with self.assertRaises(ValueError):join(*f)
 def test_missing_race(self):
  f=self.fixture();f[1]['races'].pop()
  with self.assertRaises(ValueError):join(*f)
 def test_wrong_podium(self):
  f=self.fixture();f[1]['races'][0]['podium_candidate']=[2,1,3]
  with self.assertRaises(ValueError):join(*f)
 def test_evidence(self):
  f=self.fixture();f[1]['races'][0]['evidence']['単勝']['numbers']=[2]
  with self.assertRaises(ValueError):join(*f)
 def test_hold(self):
  f=self.fixture();f[0]['records'][0]['holds']=['name']
  with self.assertRaises(ValueError):join(*f)
 def test_not_mutated(self):
  f=self.fixture();old=copy.deepcopy(f);join(*f);self.assertEqual(f,old)
if __name__=='__main__':unittest.main()
