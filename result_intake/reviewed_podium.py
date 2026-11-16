"""Explicit independent review manifest for one saved post-race PDF only."""
SOURCE_SHA256='b7dc84de4cc904070d2247214f0754a69f0e09ccd37e40479fb57de12caa54b3'
REVIEWED_EXTRACTOR='e2120ac2099d2050731ca1d58912e2ab9cb522ba'
PODIUM={1:[1,10,13],2:[2,13,8],3:[7,1,3],4:[2,1,8],5:[4,9,1],6:[5,2,10],7:[3,14,9],8:[9,6,8],9:[13,11,5],10:[10,15,17],11:[15,1,13],12:[4,12,9]}


def join(dataset,podium_report):
    """Promote exactly the 36 independently reviewed identities; never table tails.
    Acquisition/publication times remain unknown rather than inferred from mtime.
    """
    import copy
    from result_popularity import validate
    if dataset.get('source_kind')!='post_race_results' or podium_report.get('source_kind')!='post_race_results':raise ValueError('results_only')
    validate(dataset) # Enforce existing race count and coverage contract.
    records=dataset['records']
    if len(records)!=171 or any(r.get('source_sha256')!=SOURCE_SHA256 or r.get('date')!='2026-11-14' or r.get('venue')!='東京' for r in records):raise ValueError('unreviewed_source')
    reports={}
    for report in podium_report['races']:
        race=report['race_no']
        if race in reports:raise ValueError('duplicate_podium_race')
        reports[race]=report
    if set(reports)!=set(PODIUM):raise ValueError('podium_coverage')
    ranks={}
    for race,expected in PODIUM.items():
        report=reports[race]
        if report.get('source_sha256')!=SOURCE_SHA256 or report.get('status')!='payout_and_table_agreement' or report.get('podium_candidate')!=expected or report.get('reasons'):raise ValueError('review_manifest_disagreement')
        for label,n in [('単勝',1),('馬単',2),('3連単',3)]:
            if report.get('evidence',{}).get(label,{}).get('numbers')!=expected[:n]:raise ValueError('payout_evidence_disagreement')
        rr=[r for r in records if r['race_no']==race]
        if any(r['page']!=report['page'] for r in rr):raise ValueError('page_disagreement')
        for rank,umaban in enumerate(expected,1):
            matches=[r for r in rr if r['umaban']==umaban]
            if len(matches)!=1 or matches[0]['excluded'] or matches[0].get('holds') or matches[0].get('table_position')!=rank:raise ValueError('reviewed_identity_disagreement')
            ranks[(report['page'],race,umaban)]=rank
    out=copy.deepcopy(dataset)
    for row in out['records']:
        rank=ranks.get((row['page'],row['race_no'],row['umaban']))
        row['finish']=rank
        row['finish_status']='independently_reviewed_podium' if rank else 'excluded' if row['excluded'] else 'not_reviewed'
        row['finish_evidence_commit']=REVIEWED_EXTRACTOR if rank else None
    out.update(model_ready=False,production_ready=False,source_kind='post_race_results',
               provenance=dict(source_sha256=SOURCE_SHA256,event_date='2026-11-14',
                               acquired_at=None,published_at=None,pre_race_as_of=None,
                               time_status='not_evidenced',prediction_input_allowed=False),
               review_scope=dict(official_podium_records=36,lower_finish_records_reviewed=0,full_results_verified=False))
    out['unavailable']=['fourth_and_lower_finish','stable_horse_id','verified_popularity','source_acquired_at','source_published_at','pre_race_as_of']
    return out
