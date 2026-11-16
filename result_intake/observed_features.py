"""Descriptive observed history facts; no ability score, weight, or prediction."""
from datetime import date
from history_asof import select
GROUPS=('ability','recent','distance','course','going','pace','draw','weight_rider')
def extract(reports,*,as_of,target_start,target_race_ref):
    gate=select(reports,as_of=as_of,target_start=target_start,target_race_ref=target_race_ref)
    allowed={(r['horse_key'],r['race_ref']) for r in gate['candidates']}
    horses=[]
    for report in reports:
        rows=[r for r in report['rows'] if (r['horse_key'],r['race_ref']) in allowed]
        rows.sort(key=lambda r:(r['event_date'],r['race_ref']),reverse=True)
        known=[r for r in rows if r['finish'] is not None]
        by_distance={};by_course={};by_going={}
        for r in rows:
            distance=r.get('distance_m');surface=r.get('surface')
            if distance is not None:
                if type(distance) is not int or not 100<=distance<=9999:raise ValueError('distance')
                if surface not in ('芝','ダ','障'):raise ValueError('surface')
                key=f'{surface}:{distance}';by_distance[key]=by_distance.get(key,0)+1
            venue=r.get('venue');going=r.get('going')
            if isinstance(venue,str) and venue.strip():by_course[venue]=by_course.get(venue,0)+1
            if isinstance(going,str) and going.strip():by_going[going]=by_going.get(going,0)+1
        evidence=[{k:r[k] for k in ('race_ref','event_date','source_url','source_sha256','observed_at')} for r in rows]
        latest=None if not rows else dict(event_date=rows[0]['event_date'],finish=rows[0]['finish'],field_size=rows[0]['field_size'])
        facts=dict(observed_runs=len(rows),known_finish_runs=len(known),wins=sum(r['finish']==1 for r in known),top3_count=sum(r['finish']<=3 for r in known),latest_run=latest,
                   observed_distance_counts=by_distance,observed_venue_counts=by_course,observed_going_counts=by_going)
        group_status={g:dict(score=None,state='missing',reason='not_derivable_from_available_history') for g in GROUPS}
        for g in ('recent','distance','course','going'):
            group_status[g]=dict(score=None,state='descriptive_only' if rows else 'missing',reason='facts_available_but_no_validated_scoring_or_target_conditions')
        horses.append(dict(horse_key=report['horse_key'],as_of=as_of,facts=facts,evidence=evidence,groups=group_status))
    return dict(schema='observed-history-facts-v1',as_of=as_of,target_start=target_start,target_race_ref=target_race_ref,horses=horses,
        excluded=gate['excluded'],holds=gate['holds']+['validated_feature_scoring_missing','model_training_calibration_missing'],
        model_ready=False,production_ready=False,prediction_executed=False,synthetic=False)
