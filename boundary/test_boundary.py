import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from prediction_boundary import *
from datetime import timezone,timedelta
from dataclasses import replace
T=datetime(2140,4,11,10,tzinfo=timezone.utc)
def req(mode='synthetic_test'):
 return PredictionRequest('2140-04-11/R05',T,T+timedelta(hours=1),mode,tuple(Observation(k,'2140-04-11/R05','fixture://explicit',T-timedelta(minutes=1),T-timedelta(minutes=2),True,mode=='synthetic_test',(1,)) for k in sorted(REQUIRED)))
class Fake:
 def __init__(self):self.calls=0
 def predict(self,r):self.calls+=1;return ModelOutput('dummy-fixed','test-only',True,(1,))
class Tests(unittest.TestCase):
 def held(self,r,reason):
  m=Fake();out=evaluate(r,m);self.assertEqual(out['status'],'held');self.assertIn(reason,out['reasons']);self.assertEqual(m.calls,0)
 def test_synthetic_label(self):
  o=evaluate(req(),Fake());self.assertEqual(o['status'],'boundary_only_not_adopted');self.assertFalse(o['eligible_for_official_performance']);self.assertFalse(o['order_created'])
 def test_real_no_model(self):self.assertIn('payload_schema_unimplemented',evaluate(req('real_shadow'),None)['reasons'])
 def test_future(self):
  r=req();self.held(replace(r,observations=(replace(r.observations[0],available_at=T+timedelta(seconds=1)),)+r.observations[1:]),'future_information')
 def test_result_input(self):
  r=req();self.held(replace(r,observations=r.observations+(replace(r.observations[0],kind='payout'),)),'unapproved_input_kind')
 def test_mix(self):
  r=req('real_shadow');self.held(replace(r,observations=(replace(r.observations[0],synthetic=True),)+r.observations[1:]),'synthetic_real_mixture')
 def test_no_time(self):
  r=req();self.held(replace(r,observations=(replace(r.observations[0],observed_at=None),)+r.observations[1:]),'missing_time_evidence')
 def test_no_source(self):
  r=req();self.held(replace(r,observations=(replace(r.observations[0],source=''),)+r.observations[1:]),'missing_source')
 def test_no_permission(self):
  r=req();self.held(replace(r,observations=(replace(r.observations[0],usage_verified=False),)+r.observations[1:]),'usage_unverified')
 def test_after_start(self):self.held(replace(req(),scheduled_start=T),'not_pre_start')
 def test_missing(self):self.held(replace(req(),observations=()),'missing_required_observation')
 def test_wrong_race(self):
  r=req();self.held(replace(r,observations=(replace(r.observations[0],race_key='other'),)+r.observations[1:]),'race_identity_mismatch')
 def test_timezone(self):self.held(replace(req(),as_of=T.replace(tzinfo=None)),'timezone_unverified')
 def test_dummy_real_rejected(self):self.assertEqual(evaluate(req('real_shadow'),Fake())['status'],'held')
class ReviewTests(unittest.TestCase):
 held=Tests.held
 def mutate(self,**kw):
  r=req();return replace(r,observations=(replace(r.observations[0],**kw),)+r.observations[1:])
 def test_string_false(self):self.held(self.mutate(usage_verified='false'),'usage_unverified')
 def test_blank_source(self):self.held(self.mutate(source=' '),'missing_source')
 def test_int_bool(self):self.held(self.mutate(synthetic=1),'synthetic_real_mixture')
 def test_none_asof(self):self.held(replace(req(),as_of=None),'timezone_unverified')
 def test_result_nested(self):self.held(self.mutate(payload=({'nested':{'payout':9999,'result':'winner'}},)),'result_payload_forbidden')
 def test_real_unverified_schema(self):self.held(req('real_shadow'),'payload_schema_unimplemented')
if __name__=='__main__':unittest.main()
