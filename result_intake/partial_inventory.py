"""Project unknown-free identifiers for independent review, never model input."""
import re
def clean(value):
    return isinstance(value,str) and bool(value) and not re.search(r'\{[^}]*\}|\ufffd',value)
def project(inventory):
    records=[]
    for race in inventory['races']:
        issues=race['issues']
        valid_identity=(clean(race.get('date')) and clean(race.get('venue')) and
                        type(race.get('race_no')) is int and 1<=race['race_no']<=12 and
                        not any(x in issues for x in ('invalid_date','invalid_race_number','duplicate_race_identity')))
        if not valid_identity:continue
        for row in race['rows']:
            if not clean(row.get('name_candidate')):continue
            if type(row.get('umaban')) is not int or not 1<=row['umaban']<=18:continue
            if 'duplicate_runner' in issues:continue
            records.append({'date':race['date'],'venue':race['venue'],'race_no':race['race_no'],
                            'umaban':row['umaban'],'name_candidate':row['name_candidate'],
                            'runner_status':row['runner_status'],'page':race['page'],
                            'name_source_indices':row['name_source_indices'],
                            'status':'independent_review_pending','horse_id':None})
    return {'schema':'partial-review-inventory-v1','records':records,'model_ready':False,
            'excludes':['finish','time','odds','payout','sex','owner','trainer','eligibility'],
            'source_status':'full_record_on_hold'}
