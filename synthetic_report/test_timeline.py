import unittest,copy
from datetime import datetime,timezone,timedelta
from timeline import build,render
T=datetime(2140,1,1,tzinfo=timezone.utc)
def p(i=0,ids=None,**kw):
    return dict(pred_id=str(i),race_key='r',method='m',version='v',at=T+timedelta(minutes=i),mode='synthetic_only',official_performance_eligible=False,output={'trio_candidates':[{'horses':x} for x in (ids or [['1','2','3']])]},**kw)
class Tests(unittest.TestCase):
 def test_difference_and_readonly(self):
    rows=[p(1,[['2','3','4']]),p()];before=copy.deepcopy(rows)
    x=build(rows)[0]['entries'];self.assertIsNone(x[0]['added']);self.assertEqual(x[1]['added'],[('2','3','4')]);self.assertEqual(x[1]['removed'],[('1','2','3')]);self.assertEqual(rows,before)
 def test_groups_filter(self):
    a=p();b=p(1);b['version']='v2';self.assertEqual(len(build([a,b])),2);self.assertEqual(build([a], 'other'),[])
 def test_ambiguous_and_real(self):
    a=p();b=p();b['pred_id']='b'
    with self.assertRaises(ValueError):build([a,b])
    a['mode']='real'
    with self.assertRaises(ValueError):build([a])
 def test_escape_and_unordered(self):
    a=p();a['race_key']='<script>x</script>'
    b=p(1,[['3','1','2']]);b['race_key']=a['race_key']
    x=build([a,b]);self.assertEqual(x[0]['entries'][1]['added'],[]);self.assertNotIn('<script>',render(x));self.assertIn('変化理由は保存されていない',render(x))
if __name__=='__main__':unittest.main()
