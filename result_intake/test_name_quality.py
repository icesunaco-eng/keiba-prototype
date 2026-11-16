import unittest
from name_quality import compare_names,prediction_gate
H='a'*64
def row():return dict(page=1,race_no=1,umaban=1,name='架空馬',pdfkit_name='架 空 馬')
class Tests(unittest.TestCase):
 def test_agreement(self):
  r=compare_names([row()],H,H);self.assertEqual(r['agreed'],1);self.assertFalse(r['model_ready'])
 def test_disagreement(self):
  r=row();r['pdfkit_name']='別馬';self.assertEqual(compare_names([r],H,H)['holds'],1)
 def test_missing(self):
  r=row();r['pdfkit_name']='';self.assertEqual(compare_names([r],H,H)['holds'],1)
 def test_unknown(self):
  r=row();r['name']='{c001}';self.assertEqual(compare_names([r],H,H)['holds'],1)
 def test_duplicate(self):
  self.assertEqual(compare_names([row(),row()],H,H)['holds'],2)
 def test_hash(self):
  with self.assertRaises(ValueError):compare_names([row()],H,'b'*64)
 def test_prediction_rejects_results_even_pass(self):
  r=compare_names([row()],H,H);self.assertFalse(prediction_gate('post_race_results',r)['allowed'])
 def test_prediction_rejects_unverified_prerace(self):
  self.assertFalse(prediction_gate('pre_race',{})['allowed'])
if __name__=='__main__':unittest.main()
