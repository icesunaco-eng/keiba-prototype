import unittest,copy
from structure_races import structure_pages

def page():
 def p(x,y,t):return dict(x=x,y=y,t=t,f='test')
 return {'page_number':1,'unknown_glyph_occurrences':0,'pieces':[p(18.7,530.3,'30025'),p(41.1,530.3,'11月14日'),p(137.8,530.3,'2026年5東京'),p(180,530.3,'第3日第1競走'),p(21,460,'1'),p(22,460,'1'),p(49.5,460,'架空馬'),p(63,400,'（1頭）')]}
class Tests(unittest.TestCase):
 def test_candidate(self):
  r=structure_pages([page()]);self.assertEqual(r['races'][0]['status'],'candidate');self.assertFalse(r['model_ready']);self.assertIsNone(r['races'][0]['rows'][0]['finish'])
 def test_unknown(self):
  p=page();p['unknown_glyph_occurrences']=1;self.assertEqual(structure_pages([p])['races'][0]['status'],'hold')
 def test_duplicate(self):
  p=page();r=structure_pages([p,p]);self.assertTrue(all('duplicate_race_identity' in x['issues'] for x in r['races']))
 def test_nonfinite(self):
  p=page();p['pieces'][0]['x']=float('inf');self.assertEqual(structure_pages([p])['page_holds'][0]['reason'],'invalid_fragments')
 def test_count(self):
  p=page();p['pieces'][-1]['t']='（2頭）';self.assertIn('row_count_mismatch',structure_pages([p])['races'][0]['issues'])
 def test_false_label(self):
  p=page();p['pieces'][0]['y']=200;self.assertEqual(structure_pages([p])['races'],[])
 def test_split_header(self):
  p=page();p['pieces']=p['pieces'][1:]+[dict(x=18.7+i*.5,y=530.3,t=t,f='test') for i,t in enumerate('30025')];self.assertEqual(structure_pages([p])['races'][0]['race_no'],1)
 def test_right_shift(self):
  p=page();p['pieces']=[dict(x=x['x']+410.5,y=x['y'],t=x['t'],f=x['f']) for x in p['pieces']];self.assertEqual(structure_pages([p])['races'][0]['race_no'],1)
 def test_explicit_exclusion(self):
  p=page();p['pieces'] += [dict(x=x,y=y,t=t,f='test') for x,y,t in [(21,380,'2'),(22,380,'2'),(50,380,'除外馬'),(310,380.4,'（競走除外）')]]
  r=structure_pages([p])['races'][0]
  self.assertEqual(r['excluded_count'],1);self.assertEqual(r['listed_count'],2);self.assertNotIn('row_count_mismatch',r['issues'])
  self.assertEqual(r['rows'][1]['runner_status'],'excluded');self.assertIsNone(r['rows'][1]['finish'])
 def test_remote_exclusion_not_assigned(self):
  p=page();p['pieces'] += [dict(x=310,y=100,t='（競走除外）',f='test')]
  self.assertEqual(structure_pages([p])['races'][0]['excluded_count'],0)
 def test_unknown_status_not_excluded(self):
  p=page();p['pieces'] += [dict(x=310,y=460,t='（競�除外）',f='test')]
  self.assertEqual(structure_pages([p])['races'][0]['excluded_count'],0)
 def test_name_offset_excludes_sex(self):
  p=page();p['pieces'][6]['y']=459.6;p['pieces'].append(dict(x=111.2,y=460,t='牝4',f='test'))
  self.assertEqual(structure_pages([p])['races'][0]['rows'][0]['name_candidate'],'架空馬')
 def test_no_mutation(self):
  p=page();old=copy.deepcopy(p);structure_pages([p]);self.assertEqual(p,old)
if __name__=='__main__':unittest.main()
