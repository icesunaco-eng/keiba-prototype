"""Offline forward-input gap checklist. Never promotes a historical snapshot."""
from datetime import date
from history_asof import instant

def checklist(card,inventory,*,as_of,target_start,target_event_date):
    cutoff=instant(as_of);start=instant(target_start)
    if cutoff>=start:raise ValueError('prestart_cutoff')
    from zoneinfo import ZoneInfo
    if start.astimezone(ZoneInfo('Asia/Tokyo')).date()!=date.fromisoformat(target_event_date):raise ValueError('target_date')
    if card['source_sha256']!=inventory['races'][0]['source_sha256']:raise ValueError('source_mismatch')
    summaries={r['race_number']:r for r in inventory['races']}
    if len(summaries)!=len(inventory['races']):raise ValueError('duplicate_race')
    seen=set();rows=[];counts={}
    held={h['race_number'] for h in card['race_holds']}
    for row in card['records']:
        key=(row['race_number'],row['horse_number'])
        if key in seen or key[0] not in summaries:raise ValueError('identity_coverage')
        seen.add(key);counts[key[0]]=counts.get(key[0],0)+1
        if row['source_sha256']!=card['source_sha256']:raise ValueError('row_source')
        observed=instant(row['observed_at'])
        reasons=[]
        if inventory['event_date']!=target_event_date:reasons.append('card_event_is_not_target_event')
        if observed>cutoff:reasons.append('card_observed_after_cutoff')
        # Historical dates do not become forward snapshots just by moving the cutoff.
        card_start=instant(summaries[key[0]]['post_time_candidate'])
        if observed>=card_start:reasons.append('card_acquired_after_its_race_start')
        if row['holds'] or key[0] in held:reasons.append('source_extraction_hold')
        reasons.extend(['stable_horse_id_unresolved','target_entry_independent_review_required','timestamped_prior_history_required','feature_validation_required','trained_calibrated_model_required'])
        rows.append(dict(race_number=key[0],horse_number=key[1],horse_name_candidate=row['horse_name_candidate'],missing=reasons,
                         next_checks=['match official target-card profile ID','cross-check candidate against independent source','collect permitted prior snapshots with acquisition evidence','validate feature completeness and timing'],ready=False))
    if any(counts.get(n,0)!=r['listed_count'] for n,r in summaries.items()) or len(rows)!=card['listed_count']:raise ValueError('horse_count_coverage')
    summary={}
    for row in rows:
        for reason in row['missing']:summary[reason]=summary.get(reason,0)+1
    return dict(schema='forward-input-gap-checklist-v1',target_event_date=target_event_date,as_of=as_of,target_start=target_start,source_event_date=inventory['event_date'],
        rows=rows,gap_counts=summary,ready_horses=0,model_ready=False,production_ready=False,prediction_executed=False)
