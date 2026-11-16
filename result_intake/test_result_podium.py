import unittest
from result_podium import extract
class Tests(unittest.TestCase):
 def fixture(self):
  ps=[dict(x=50,y=100-i*10,t='horse',f='C') for i in range(3)]
  for label,raw,x,y in [('単勝','①',49.7,50),('馬単','①②',231,40),('3連単','①②③',231,20)]:
   ps.extend([dict(x=x,y=y,t=label,f='C'),dict(x=x+28.2,y=y-.4,t=raw,f='T')])
  race=dict(page=1,race_no=1,panel_origin=18.7,rows=[dict(umaban=i+1,runner_status='unclassified',name_source_indices=[i]) for i in range(3)])
  return [dict(races=[race]),[dict(page_number=1,pieces=ps)],'a'*64]
 def test_ok(self):
  x=extract(*self.fixture());self.assertEqual(x['races'][0]['podium_candidate'],[1,2,3]);self.assertFalse(x['races'][0]['official_finish_verified']);self.assertFalse(x['model_ready'])
 def test_missing(self):
  f=self.fixture();f[1][0]['pieces'].pop();self.assertIsNone(extract(*f)['races'][0]['podium_candidate'])
 def test_duplicate_combination(self):
  f=self.fixture();f[1][0]['pieces'][-1]['t']='①①③';self.assertEqual(extract(*f)['races'][0]['status'],'hold')
 def test_disagree(self):
  f=self.fixture();f[1][0]['pieces'][-1]['t']='①③②';self.assertEqual(extract(*f)['races'][0]['status'],'hold')
 def test_extra_label(self):
  f=self.fixture();f[1][0]['pieces'].append(dict(f[1][0]['pieces'][-2]));self.assertEqual(extract(*f)['races'][0]['status'],'hold')
 def test_opaque(self):
  f=self.fixture();f[1][0]['pieces'][-1]['t']='{c033}';self.assertEqual(extract(*f)['races'][0]['status'],'hold')
 def test_excluded(self):
  f=self.fixture();f[0]['races'][0]['rows'][0]['runner_status']='excluded';self.assertEqual(extract(*f)['races'][0]['status'],'hold')
 def test_sales_label_not_payout(self):
  f=self.fixture();f[1][0]['pieces'][-2]['t']='3連単：';self.assertEqual(extract(*f)['races'][0]['status'],'hold')
 def test_wrong_panel(self):
  f=self.fixture();f[1][0]['pieces'][-2]['x']+=411.1;self.assertEqual(extract(*f)['races'][0]['status'],'hold')
if __name__=='__main__':unittest.main()
