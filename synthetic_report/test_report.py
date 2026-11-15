import json, sqlite3, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone
from report import load, render, digest, encode

def event():
    p = dict(pred_id='p1', race_key='2140-SYN<script>', method='synthetic-score-pl-v1', version='isolated-v1', at=datetime(2140,1,1,tzinfo=timezone.utc), mode='synthetic_only', official_performance_eligible=False, output={'trio_candidates':[{'horses':['1','2','3']}]} )
    e = dict(schema=2, request={'synthetic':True}, saved_evidence={'predictions':[p], 'comparisons':[]})
    e['evidence_hash']=digest(e)
    return e

class Tests(unittest.TestCase):
    def db(self, path, events):
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY,payload TEXT)')
            db.executemany('INSERT INTO events(payload) VALUES (?)', [(json.dumps(e,default=encode),) for e in events])
    def test_readonly_dedupe_escape(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'j.db';e=event();self.db(p,[e,e]);before=p.read_bytes()
            ps,cs=load(p);self.assertEqual(len(ps),1);out=render(ps,cs)
            self.assertIn('&lt;script&gt;',out);self.assertNotIn('<script>',out)
            self.assertIn('結果未比較',out);self.assertIn('未較正',out);self.assertEqual(p.read_bytes(),before)
    def test_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'j.db';e=event();e['saved_evidence']['comparisons']=[dict(pred_id='p1',synthetic=True,official_performance_eligible=False,candidate_rank=2)];e['evidence_hash']=digest({k:v for k,v in e.items() if k!='evidence_hash'});self.db(p,[e]);self.assertIn('候補順位：2',render(*load(p)))
    def test_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'j.db';e=event();e['request']['synthetic']=False;self.db(p,[e]);before=p.read_bytes()
            with self.assertRaises(ValueError):load(p)
            self.assertEqual(p.read_bytes(),before)
    def test_real_rejected_even_hash_matches(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'j.db';e=event();e['request']['synthetic']=False;e['evidence_hash']=digest({k:v for k,v in e.items() if k!='evidence_hash'});self.db(p,[e])
            with self.assertRaises(ValueError):load(p)
    def test_missing_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'missing.db'
            with self.assertRaises(sqlite3.Error):load(p)
            self.assertFalse(p.exists())
    def test_empty(self):
        self.assertIn('保存済み予測はありません',render([],{}))
if __name__=='__main__':unittest.main()
