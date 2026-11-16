"""Offline descriptive prior-run features, NOT future probabilities or a predictor."""
from datetime import datetime
import hashlib,json

def aware(t):
    return isinstance(t,datetime) and t.utcoffset() is not None

def extract(horse_id, target_race, as_of, target_start, rows):
    if not all(type(s) is str and s.strip() for s in (horse_id,target_race)):
        raise ValueError('identifiers required')
    if not aware(as_of) or not aware(target_start) or as_of>=target_start:
        raise ValueError('prestart aware times required')
    if type(rows) is not list: raise ValueError('rows required')
    accepted=[];excluded=[];seen={}
    fields={'race_key','horse_id','start','available_at','observed_at','finish','field_size','source_ref','source_hash'}
    for row in rows:
        if type(row) is not dict or set(row)!=fields: raise ValueError('exact normalized schema required')
        if any(type(row[k]) is not str or not row[k].strip() for k in ('race_key','horse_id','source_ref')): raise ValueError('missing provenance')
        h=row['source_hash']
        if type(h) is not str or len(h)!=64 or any(c not in '0123456789abcdef' for c in h): raise ValueError('sha256 required')
        if not all(aware(row[k]) for k in ('start','available_at','observed_at')): raise ValueError('unknown timing')
        if row['available_at']<row['start'] or row['observed_at']<row['available_at']: raise ValueError('invalid result timing')
        if type(row['field_size']) is not int or row['field_size']<1: raise ValueError('invalid field size')
        if row['finish'] is not None and (type(row['finish']) is not int or not 1<=row['finish']<=row['field_size']): raise ValueError('invalid finish')
        if row['horse_id']!=horse_id: continue
        key=(row['race_key'],row['horse_id'])
        if key in seen:
            if seen[key]!=row: raise ValueError('conflicting history; correction policy unresolved')
            continue
        seen[key]=row
        reasons=[]
        if row['race_key']==target_race: reasons.append('target_race')
        if row['start'].astimezone(target_start.tzinfo).date()>=target_start.date(): reasons.append('not_prior_date')
        if row['start']>=as_of or row['available_at']>as_of or row['observed_at']>as_of: reasons.append('not_known_at_prediction')
        if reasons:
            excluded.append({'race_key':row['race_key'],'reasons':reasons});continue
        accepted.append(row)
    accepted.sort(key=lambda r:(r['start'],r['race_key']))
    known=[r for r in accepted if r['finish'] is not None]
    canonical=json.dumps(accepted,default=lambda t:t.isoformat(),sort_keys=True,separators=(',',':'))
    return {'schema':'prior-history-descriptive-v1','horse_id':horse_id,'target_race':target_race,
            'as_of':as_of.isoformat(),'runs':len(accepted),'known_finish_runs':len(known),
            'missing_finish_runs':len(accepted)-len(known),
            'wins':sum(r['finish']==1 for r in known),'top3_count':sum(r['finish']<=3 for r in known),
            'top3_observed_fraction':None if not known else {'numerator':sum(r['finish']<=3 for r in known),'denominator':len(known)},
            'state':'available_descriptive' if known else 'insufficient_history',
            'evidence':[{'race_key':r['race_key'],'source_ref':r['source_ref'],'source_hash':r['source_hash'],'observed_at':r['observed_at'].isoformat()} for r in accepted],
            'excluded':excluded,'accepted_input_hash':hashlib.sha256(canonical.encode()).hexdigest(),
            'predicted_probability':None,'score_1_to_10':None,'model_ready':False,'order_created':False}
