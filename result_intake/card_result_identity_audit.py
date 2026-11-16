"""Post-event audit only: never merge result fields into entry candidates."""
import copy

def audit(card,inventory,result):
    if card['source_sha256']!=inventory['races'][0]['source_sha256']:raise ValueError('card_source')
    if result['schema']!='postrace-staging-v1':raise ValueError('result_schema')
    scope={(inventory['event_date'],r['venue']) for r in inventory['races']}
    if len(scope)!=1:raise ValueError('card_scope')
    if any((r['date'],r['venue']) not in scope for r in result['records']):raise ValueError('result_scope')
    sources={r['source_sha256'] for r in result['records']}
    if len(sources)!=1 or card['source_sha256'] in sources:raise ValueError('distinct_source_required')
    def keyed(rows,racekey,numkey):
        out={}
        for row in rows:
            key=(row[racekey],row[numkey])
            if key in out:raise ValueError('duplicate_identity')
            out[key]=row
        return out
    left=keyed(card['records'],'race_number','horse_number');right=keyed(result['records'],'race_no','umaban')
    race_holds=card.get('race_holds')
    if not isinstance(race_holds,list):raise ValueError('race_holds_required')
    held_races={}
    for hold in race_holds:
        if not isinstance(hold,dict) or type(hold.get('race_number')) is not int or not isinstance(hold.get('reason'),str):raise ValueError('race_hold_schema')
        held_races.setdefault(hold['race_number'],[]).append(hold['reason'])
    comparisons=[]
    for key in sorted(set(left)|set(right)):
        a=left.get(key);b=right.get(key)
        if a is None or b is None:state='missing_counterpart'
        elif key[0] in held_races:state='held_source'
        elif a['holds'] or not a['horse_name_candidate'] or b.get('holds'):state='held_source'
        elif a['horse_name_candidate']==b['name']:state='name_and_number_agree'
        else:state='name_difference'
        comparisons.append(dict(race_number=key[0],horse_number=key[1],state=state,race_hold_reasons=held_races.get(key[0],[]),card_name=None if a is None else a['horse_name_candidate'],result_name=None if b is None else b['name']))
    return dict(schema='postevent-card-identity-audit-v1',comparisons=comparisons,agreement_count=sum(x['state']=='name_and_number_agree' for x in comparisons),
        card_source_sha256=card['source_sha256'],result_source_sha256=next(iter(sources)),
        birthdate_verified=False,sex_age_verified=False,result_fields_merged=False,prestart_evidence=False,model_ready=False,production_ready=False)
