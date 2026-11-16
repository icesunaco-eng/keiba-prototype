import unittest
from partial_inventory import project
from structure_races import structure_pages
from test_structure_races import page
class Tests(unittest.TestCase):
 def test_unknown_outside_name(self):
  p=page();p['pieces'].append(dict(x=300,y=460,t='{c033}',f='x'))
  r=project(structure_pages([p]));self.assertEqual(len(r['records']),1);self.assertFalse(r['model_ready'])
 def test_unknown_name(self):
  p=page();p['pieces'][6]['t']='架空{c033}'
  self.assertEqual(project(structure_pages([p]))['records'],[])
 def test_duplicate(self):
  p=page();self.assertEqual(project(structure_pages([p,p]))['records'],[])
if __name__=='__main__':unittest.main()
