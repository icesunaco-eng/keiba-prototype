import sys,tempfile,unittest,sqlite3
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parent))
from persistent import Journal
from model import GROUPS
T=datetime(2140,4,11,10,tzinfo=timezone.utc)
def fixture():
 req=dict(synthetic=True,as_of=T,scheduled_start=T+timedelta(minutes=15),horses=[dict(id=str(i),features={g:dict(value=9-i,observed_at=T,available_at=T,source='fabricated',target_outcome=False) for g in GROUPS}) for i in range(1,5)])
 result=dict(synthetic=True,race_key='2140-04-11/SYN01',available_at=T+timedelta(minutes=20),top3=['1','2','3'])
 return req,result
class Tests(unittest.TestCase):
 def test_restart(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'journal.sqlite';q,r=fixture()
   self.assertEqual(Journal(p).apply('SYN01',q)['state'],'committed')
   self.assertEqual(Journal(p).apply('SYN01',q,r)['events'],2)
   x=Journal(p).apply('SYN01',q,dict(r,top3=['3','2','1']))
   self.assertEqual(x['state'],'exists');self.assertEqual(x['predictions'],1);self.assertEqual(x['comparisons'],1)
 def test_failed_result_rolls_back(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'journal.sqlite';q,r=fixture()
   self.assertEqual(Journal(p).apply('SYN01',q,dict(r,synthetic=False))['state'],'held')
   self.assertEqual(Journal(p).apply('SYN01',q,r)['events'],1)
   before=p.read_bytes();self.assertEqual(Journal(p).apply('SYN01',q,dict(r,top3=['2','3','4']))['state'],'held');self.assertEqual(p.read_bytes(),before)
 def test_corruption_preserved(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'journal.sqlite';p.write_bytes(b'not a database');q,r=fixture()
   self.assertEqual(Journal(p).apply('SYN01',q,r)['state'],'held');self.assertEqual(p.read_bytes(),b'not a database')
 def test_invalid_path(self):
  q,r=fixture();self.assertEqual(Journal('/nonexistent-parent-keiba-test/journal.sqlite').apply('SYN01',q,r)['state'],'held')
 def test_locked_and_malformed_journal(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'journal.sqlite';q,r=fixture();Journal(p).apply('SYN01',q,r)
   db=sqlite3.connect(p);db.execute('BEGIN IMMEDIATE')
   self.assertEqual(Journal(p).apply('SYN01',q,r)['state'],'held');db.rollback()
   db.execute("INSERT INTO events(payload) VALUES ('broken-json')");db.commit();db.close()
   before=p.read_bytes();self.assertEqual(Journal(p).apply('SYN01',q,r)['state'],'held');self.assertEqual(p.read_bytes(),before)
 def test_fixed_output_and_changed_code(self):
  import persistent
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'journal.sqlite';q,r=fixture();j=Journal(p);j.apply('SYN01',q,r)
   before=p.read_bytes();saved=j.read_saved();self.assertEqual(saved['state'],'read_only');self.assertFalse(saved['recomputed'])
   ev=saved['events'][0];self.assertEqual(ev['saved_evidence']['predictions'][0]['method'],'synthetic-score-pl-v1');self.assertIn('model.py',ev['code_binding']);self.assertEqual(len(ev['input_hash']),64)
   with patch.object(persistent,'code_binding',return_value={'different':'version'}),patch.object(persistent.Bridge,'save',side_effect=AssertionError('must not recompute')):
    self.assertEqual(j.apply('SYN01',q,r)['state'],'held');self.assertEqual(j.read_saved(),saved)
   self.assertEqual(p.read_bytes(),before)
 def test_modified_evidence_detected(self):
  import json
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'journal.sqlite';q,r=fixture();j=Journal(p);j.apply('SYN01',q,r)
   db=sqlite3.connect(p);payload=json.loads(db.execute('SELECT payload FROM events').fetchone()[0]);payload['saved_evidence']['predictions'][0]['method']='changed';db.execute('UPDATE events SET payload=?',(json.dumps(payload),));db.commit();db.close()
   self.assertEqual(j.read_saved()['state'],'held');self.assertEqual(j.apply('SYN01',q,r)['state'],'held')
if __name__=='__main__':unittest.main()
