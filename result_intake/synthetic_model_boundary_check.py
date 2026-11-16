"""Local proof with fabricated pre-race entries and features, not real history."""
import importlib.util
from datetime import datetime,timedelta,timezone
from pathlib import Path

def check(model_path):
    spec=importlib.util.spec_from_file_location('existing_synthetic_model',model_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    at=datetime(2140,4,11,10,tzinfo=timezone.utc);start=at+timedelta(minutes=15)
    entry={'synthetic':True,'observed_at':at-timedelta(hours=1),'scheduled_start':start,'horses':['SYN1','SYN2','SYN3','SYN4']}
    request=dict(synthetic=True,as_of=at,scheduled_start=entry['scheduled_start'],horses=[dict(id=key,features={g:dict(value=5+i,observed_at=entry['observed_at'],available_at=entry['observed_at'],source='fabricated_fixture_not_jra',target_outcome=False) for g in module.GROUPS}) for i,key in enumerate(entry['horses'])])
    result=module.predict(request)
    assert result['state']=='synthetic_rule_prototype'
    assert set(result['scores'])==set(entry['horses'])
    assert result['order_created'] is False
    real=dict(request,synthetic=False);assert module.predict(real)['state']=='held'
    return dict(synthetic_only=True,fictional_entries=4,score_rows=len(result['scores']),trio_rows=len(result['trio_candidates']),real_input_rejected=True,order_created=False,model_calibrated=False)
if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser();p.add_argument('model_path');a=p.parse_args();print(json.dumps(check(Path(a.model_path))))
