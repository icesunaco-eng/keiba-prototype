"""Race-level consistency validation, not independent proof of glyph values.
Never infer popularity from rounded odds. Equal displayed odds are permitted.
"""
import math

def validate(dataset):
    if dataset.get('source_kind')!='post_race_results':raise ValueError('results_only')
    groups={};identities=set()
    for row in dataset['records']:
        key=(row['page'],row['race_no'],row['umaban'])
        if key in identities:raise ValueError('duplicate_identity')
        identities.add(key);groups.setdefault(key[:2],[]).append(row)
    summaries=[];rows=[]
    for key,items in groups.items():
        active=[r for r in items if not r['excluded']]
        values=[r.get('popularity_candidate') for r in active]
        reasons=[]
        if not active or any(type(v) is not int for v in values) or sorted(values)!=list(range(1,len(active)+1)):
            reasons.append('popularity_not_complete_permutation')
        if any(r.get('popularity_candidate') is not None for r in items if r['excluded']):reasons.append('excluded_has_popularity')
        if any(r.get('holds') or r.get('status')!='verified_columns_only' for r in items):reasons.append('upstream_hold')
        odds=[r.get('win_odds') for r in active]
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in odds):reasons.append('invalid_odds')
        elif not reasons:
            ranked=sorted(active,key=lambda r:r['popularity_candidate'])
            if any(a['win_odds']>b['win_odds'] for a,b in zip(ranked,ranked[1:])):reasons.append('odds_popularity_inversion')
        status='hold' if reasons else 'race_consistency_passed'
        summaries.append(dict(page=key[0],race_no=key[1],starters=len(active),status=status,reasons=reasons))
        for r in items:
            rows.append(dict(page=key[0],race_no=key[1],umaban=r['umaban'],popularity_candidate=r.get('popularity_candidate'),
                             status='excluded' if r['excluded'] else status,popularity_verified=False))
    return dict(rows=rows,race_summary=summaries,source_kind='post_race_results',
                method='reviewed_font_candidates_plus_complete_permutation_and_odds_order',
                limitations=['Consistency does not independently verify each glyph or official finishing order.'],
                model_ready=False,production_ready=False)
