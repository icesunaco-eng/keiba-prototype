"""Join independently parsed identity evidence with conditional historical snapshots.
Always hypothetical staging; not a prediction runner or confirmed-entry verifier.
"""
import hashlib,json
from profile_history import batch
from profile_identity import match
from history_asof import select,instant

def build(card_raw,card_meta,saved_profiles,*,as_of,target_start,target_race_ref):
    if not isinstance(saved_profiles,list) or not saved_profiles:raise ValueError('profiles_required')
    reports=batch(saved_profiles)
    identities=[match(card_raw,card_meta,raw,meta) for raw,meta in saved_profiles]
    gate=select(reports,as_of=as_of,target_start=target_start,target_race_ref=target_race_ref)
    cutoff=instant(as_of)
    index={x['horse_key']:x for x in identities}
    if len(index)!=len(identities) or set(index)!={x['horse_key'] for x in reports}:raise ValueError('identity_coverage')
    numbers=[x['card_number'] for x in identities]
    if len(numbers)!=len(set(numbers)):raise ValueError('duplicate_card_number')
    candidates=[];held=[]
    for row in gate['candidates']:
        identity=index[row['horse_key']]
        if identity['profile_sha256']!=row['source_sha256'] or identity['profile_observed_at']!=row['observed_at']:raise ValueError('identity_snapshot_mismatch')
        reasons=[]
        if instant(identity['card_observed_at'])>cutoff:reasons.append('identity_card_not_observed_by_cutoff')
        combined=dict(row,identity_evidence=identity)
        if reasons:held.append(dict(combined,reasons=reasons))
        else:candidates.append(combined)
    return dict(schema='identity-history-staging-v1',as_of=as_of,target_start=target_start,target_race_ref=target_race_ref,
        target_is_hypothetical=True,profiles=len(reports),identity_matches=len(identities),candidates=candidates,
        excluded_history=gate['excluded'],held_identity=held,
        holds=['confirmed_target_entry_snapshot_required','target_entry_identity_crosscheck_required'],
        note='Identity is matched only to the supplied historical card, not to a future target entry.',
        input_sha256=gate['input_sha256'],card_sha256=card_meta['sha256'],
        model_ready=False,production_ready=False,prediction_executed=False,order_created=False)
