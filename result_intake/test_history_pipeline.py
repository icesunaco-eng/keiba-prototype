import unittest,tempfile,json
from pathlib import Path
import test_profile_history
from history_pipeline import run
class Tests(unittest.TestCase):
 def manifest(self):return dict(schema='saved-profile-manifest-v1',profiles=[dict(html='one.html',metadata='one.json')],as_of='2026-11-17T00:00:00Z',target_start='2026-11-21T01:00:00Z',target_race_ref='hypothetical')
 def test_offline_pipeline(self):
  with tempfile.TemporaryDirectory() as d:
   raw,meta=test_profile_history.Tests().fixture();Path(d,'one.html').write_bytes(raw);Path(d,'one.json').write_text(json.dumps(meta))
   x=run(self.manifest(),d);self.assertEqual(x['profile_count'],1);self.assertEqual(x['history_row_count'],1);self.assertEqual(len(x['candidates']),1);self.assertFalse(x['model_ready']);self.assertTrue(x['target_is_hypothetical'])
 def test_escape(self):
  m=self.manifest();m['profiles'][0]['html']='../outside'
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(ValueError,'snapshot_path_outside_root'):run(m,d)
 def test_empty(self):
  m=self.manifest();m['profiles']=[]
  with self.assertRaisesRegex(ValueError,'profiles_required'):run(m,'.')
if __name__=='__main__':unittest.main()
