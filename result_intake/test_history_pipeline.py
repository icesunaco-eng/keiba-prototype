import unittest,tempfile,json
from unittest.mock import patch
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
 def test_bad_manifest_before_read(self):
  for value in (None,[],{},dict(self.manifest(),extra=1)):
   with self.subTest(value=value),patch.object(Path,'read_bytes',side_effect=AssertionError('read')):
    with self.assertRaisesRegex(ValueError,'manifest_fields'):run(value,'.')
 def test_duplicate_before_read(self):
  m=self.manifest();m['profiles'].append(dict(html='./one.html',metadata='two.json'))
  with patch.object(Path,'read_bytes',side_effect=AssertionError('read')):
   with self.assertRaisesRegex(ValueError,'duplicate_snapshot_path'):run(m,'.')
 def test_limit_before_read(self):
  m=self.manifest();m['profiles']*=100
  with patch.object(Path,'read_bytes',side_effect=AssertionError('read')):
   with self.assertRaisesRegex(ValueError,'profile_limit'):run(m,'.')
 def test_invalid_second_item_before_read(self):
  m=self.manifest();m['profiles'].append(dict(html=None,metadata='two.json'))
  with patch.object(Path,'read_bytes',side_effect=AssertionError('read')):
   with self.assertRaisesRegex(ValueError,'snapshot_path_required'):run(m,'.')
 def test_missing_file_diagnostic(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(ValueError,'snapshot_read_or_json_failed'):run(self.manifest(),d)
 def test_metadata_schema(self):
  with tempfile.TemporaryDirectory() as d:
   Path(d,'one.html').write_bytes(b'hello');Path(d,'one.json').write_text('null')
   with self.assertRaisesRegex(ValueError,'metadata_fields'):run(self.manifest(),d)
 def test_bad_times_before_read(self):
  m=self.manifest();m['as_of']=None
  with patch.object(Path,'read_bytes',side_effect=AssertionError('read')):
   with self.assertRaisesRegex(ValueError,'manifest_times'):run(m,'.')
if __name__=='__main__':unittest.main()
