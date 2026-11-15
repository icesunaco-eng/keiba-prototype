"""Isolated synthetic-only, untrained rule scoring demonstration. No betting API."""
from datetime import datetime
from itertools import combinations, permutations
from math import exp, fsum, isfinite
GROUPS=('ability','recent','distance','course','going','pace','draw','weight_rider')

def predict(request):
    def held(reason):
        return {'state':'held','reason':reason,'order_created':False,'official_performance_eligible':False}
    if type(request) is not dict:return held('invalid_request')
    if request.get('synthetic') is not True:return held('real_data_not_supported')
    if set(request)!={'synthetic','as_of','scheduled_start','horses'}:return held('unknown_or_missing_field')
    asof,start=request['as_of'],request['scheduled_start']
    def aware(t):return isinstance(t,datetime) and t.tzinfo is not None and t.utcoffset() is not None
    if not aware(asof) or not aware(start) or asof>=start:return held('invalid_prestart_time')
    horses=request['horses']
    if type(horses) is not list or not 3<=len(horses)<=18:return held('invalid_field_size')
    scores={}; details={}
    for horse in horses:
        if type(horse) is not dict or set(horse)!={'id','features'}:return held('invalid_horse_schema')
        ident=horse['id']
        if type(ident) is not str or not ident.strip() or ident in scores:return held('invalid_duplicate_id')
        features=horse['features']
        if type(features) is not dict or set(features)!=set(GROUPS):return held('incomplete_or_unknown_features')
        vals=[]
        for group in GROUPS:
            f=features[group]
            if type(f) is not dict or set(f)!={'value','observed_at','available_at','source','target_outcome'}:return held('invalid_feature_schema')
            if f['target_outcome'] is not False:return held('outcome_or_unconfirmed_origin')
            if type(f['source']) is not str or not f['source'].strip():return held('missing_source')
            if any(not aware(f[k]) or f[k]>asof for k in ('observed_at','available_at')):return held('future_or_missing_feature_time')
            val=f['value']
            if type(val) not in (int,float) or not 0<=val<=10 or not isfinite(val):return held('missing_or_invalid_score')
            vals.append(float(val))
        scores[ident]=fsum(vals)/len(GROUPS)
        details[ident]={'group_scores':dict(zip(GROUPS,vals)),'coverage':1.0}
    # Illustrative equal-weight score; temperature 2 is NOT fitted or adopted.
    top=max(scores.values()); strengths={k:exp((v-top)/2.0) for k,v in scores.items()}
    total=fsum(strengths.values()); win={k:v/total for k,v in strengths.items()}
    triples=[]
    for combo in combinations(sorted(scores),3):
        # Plackett-Luce without replacement: sum all 6 finish orders.
        p=fsum((strengths[a]/total)*(strengths[b]/fsum(strengths[x] for x in strengths if x!=a))*(strengths[c]/fsum(strengths[x] for x in strengths if x not in (a,b))) for a,b,c in permutations(combo))
        triples.append({'horses':list(combo),'model_probability':p})
    triples.sort(key=lambda x:(-x['model_probability'],x['horses']))
    return {'state':'synthetic_rule_prototype','scores':scores,'details':details,'model_win_probabilities':win,'trio_candidates':triples,'probability_label':'uncalibrated_model_assumption_not_empirical_probability','method':'8-group equal mean; softmax temperature 2; Plackett-Luce top3','order_created':False,'official_performance_eligible':False,'real_shadow_supported':False}
