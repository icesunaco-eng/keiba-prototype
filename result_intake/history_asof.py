"""Conditional offline snapshot eligibility; no forecast and no invented timestamps."""
from datetime import datetime,date
from zoneinfo import ZoneInfo
import hashlib,json,re
JST=ZoneInfo('Asia/Tokyo')
def instant(value):
    if not isinstance(value,str):raise ValueError('time_required')
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.utcoffset() is None:raise ValueError('timezone_required')
    return t

def select(reports, *, as_of, target_start, target_race_ref):
    cutoff=instant(as_of);target=instant(target_start)
    if cutoff>=target:raise ValueError('prestart_cutoff_required')
    if not isinstance(target_race_ref,str) or not target_race_ref.strip():raise ValueError('target_reference_required')
    accepted=[];excluded=[];seen=set();horses=set()
    for report in reports:
        key=report['horse_key']
        if key in horses:raise ValueError('duplicate_profile')
        horses.add(key)
        if report['source_kind']!='post_race_history_snapshot':raise ValueError('source_kind')
        acquired=instant(report['observed_at'])
        for row in report['rows']:
            if row['horse_key']!=key or row['source_kind']!=report['source_kind']:raise ValueError('identity_or_kind')
            if instant(row['observed_at'])!=acquired:raise ValueError('snapshot_time_mismatch')
            if not re.fullmatch('[0-9a-f]{64}',row['source_sha256']):raise ValueError('source_hash')
            identity=(key,row['race_ref'])
            if identity in seen:raise ValueError('duplicate_history')
            seen.add(identity)
            # This adapter supports observed snapshots with unknown original timing only.
            if row['published_at'] is not None or row['start_at'] is not None:raise ValueError('unsupported_original_timing')
            day=date.fromisoformat(row['event_date'])
            if day>acquired.astimezone(JST).date():raise ValueError('future_result')
            if type(row['field_size']) is not int or not 1<=row['field_size']<=30:raise ValueError('field_size')
            if row['finish'] is not None and (type(row['finish']) is not int or not 1<=row['finish']<=row['field_size']):raise ValueError('finish')
            reasons=[]
            if acquired>cutoff:reasons.append('snapshot_not_observed_by_cutoff')
            if day>=target.astimezone(JST).date():reasons.append('not_prior_target_date')
            if day>=cutoff.astimezone(JST).date():reasons.append('not_prior_cutoff_date')
            if row['race_ref']==target_race_ref:reasons.append('target_race')
            evidence={k:row[k] for k in ('horse_key','race_ref','event_date','finish','field_size','source_sha256','source_url','observed_at','published_at','start_at')}
            if reasons:excluded.append(dict(evidence,reasons=reasons))
            else:accepted.append(evidence)
    # These are candidates, not cleared prediction input. No target entry or identity
    # verification is inferred merely because a URL-derived profile key is present.
    accepted.sort(key=lambda r:(r['horse_key'],r['event_date'],r['race_ref']))
    return dict(schema='conditional-history-snapshot-v1',as_of=as_of,target_start=target_start,target_race_ref=target_race_ref,
        candidates=accepted,excluded=excluded,holds=['confirmed_target_entry_snapshot_required','horse_identity_crosscheck_required'],
        timing_basis='observed_snapshot_only; original_publication_and_race_start_unknown',
        input_sha256=hashlib.sha256(json.dumps(reports,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        model_ready=False,production_ready=False,prediction_executed=False)
