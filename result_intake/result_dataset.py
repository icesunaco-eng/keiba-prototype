"""Offline post-race staging join. No storage/network/model execution."""
import re,math
from datetime import date
def _index(rows):
    out={}
    for r in rows:
        key=(r['page'],r['race_no'],r['umaban'])
        if key in out:raise ValueError('duplicate_identity')
        if any(type(v) is not int for v in key) or key[0]<1 or not 1<=key[1]<=12 or not 1<=key[2]<=18:raise ValueError('invalid_identity')
        out[key]=r
    return out
def _validate_time_references(ti,mi):
    """Reject malformed or ungrounded ditto chains before any verified output."""
    for key,row in ti.items():
        ref=row.get('time_reference')
        if row.get('status')!='referenced_time':
            if ref is not None:raise ValueError('unexpected_time_reference')
            continue
        if not isinstance(ref,(list,tuple)) or len(ref)!=3 or any(type(v) is not int for v in ref):
            raise ValueError('invalid_time_reference')
        target=tuple(ref)
        if target==key or target[:2]!=key[:2] or target not in ti:raise ValueError('invalid_time_reference')
        previous=ti[target]
        if previous.get('status') not in ('explicit_time','referenced_time'):raise ValueError('unverified_time_reference')
        values=[row.get('seconds'),previous.get('seconds')]
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values) or values[0]!=values[1]:
            raise ValueError('time_reference_value_mismatch')
        pos=mi[key].get('table_position');prev=mi[target].get('table_position')
        if type(pos) is not int or type(prev) is not int or prev<1 or pos!=prev+1:
            raise ValueError('time_reference_order')
        if sum(m.get('table_position')==prev for k,m in mi.items() if k[:2]==key[:2])!=1 or sum(m.get('table_position')==pos for k,m in mi.items() if k[:2]==key[:2])!=1:
            raise ValueError('ambiguous_time_reference_order')
    # Explicit termination check also protects against future order-policy changes.
    for key in ti:
        seen=set();current=key
        while ti[current].get('status')=='referenced_time':
            if current in seen:raise ValueError('cyclic_time_reference')
            seen.add(current);current=tuple(ti[current]['time_reference'])
        if seen and ti[current].get('status')!='explicit_time':raise ValueError('ungrounded_time_reference')

def build(races,names,times,market):
    envelopes=[names,times,market]
    hashes=[e.get('source_sha256') for e in envelopes]
    if any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes) or len(set(hashes))!=1:raise ValueError('source_hash_mismatch')
    if any(e.get('source_kind')!='post_race_results' for e in envelopes):raise ValueError('results_only')
    ni,ti,mi=[_index(e['rows']) for e in envelopes]
    if not ni or set(ni)!=set(ti) or set(ni)!=set(mi):raise ValueError('coverage_mismatch')
    _validate_time_references(ti,mi)
    ri={}
    for r in races:
        if r.get('source_sha256')!=hashes[0]:raise ValueError('race_source_hash_mismatch')
        key=(r['page'],r['race_no'])
        if key in ri:raise ValueError('duplicate_race')
        date.fromisoformat(r['date'])
        if not r['venue']:raise ValueError('missing_venue')
        ri[key]=r
    if set(ri)!={(k[0],k[1]) for k in ni}:raise ValueError('race_coverage_mismatch')
    records=[]
    for key,n in ni.items():
        t,m=ti[key],mi[key];race=ri[key[:2]];holds=[]
        name=n.get('name')
        if n.get('status')!='two_engine_agreement' or not isinstance(name,str) or not name or re.search(r'\ufffd|\{[^}]*\}',name):name=None;holds.append('name')
        excluded=t['status']=='excluded'
        if excluded!=(m['status']=='excluded'):raise ValueError('status_conflict')
        seconds=t.get('seconds') if t['status'] in ('explicit_time','referenced_time') else None
        odds=m.get('win_odds') if m['status']=='two_engine_agreement' else None
        for field,value in [('seconds',seconds),('win_odds',odds)]:
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<=0):raise ValueError('invalid_numeric')
        if not excluded:
            if seconds is None:holds.append('time')
            if odds is None:holds.append('odds')
        if m.get('umaban_check')!='two_engine_agreement':holds.append('umaban')
        records.append(dict(date=race['date'],venue=race['venue'],race_no=key[1],page=key[0],umaban=key[2],
                            name=name,seconds=seconds,win_odds=odds,excluded=excluded,
                            table_position=m.get('table_position'),finish=None,
                            popularity_candidate=m.get('popularity_candidate'),popularity_verified=False,
                            time_reference=t.get('time_reference'),holds=holds,
                            source_sha256=hashes[0],status='hold' if holds else 'verified_columns_only'))
    summary=[]
    for key,r in ri.items():
        rr=[x for x in records if (x['page'],x['race_no'])==key]
        ex=sum(x['excluded'] for x in rr)
        if len(rr)!=r['listed_count'] or ex!=r['excluded_count'] or len(rr)-ex!=r['field_size']:raise ValueError('race_count_mismatch')
        summary.append(dict(date=r['date'],venue=r['venue'],race_no=r['race_no'],listed=len(rr),starters=len(rr)-ex,excluded=ex,
                            time_count=sum(x['seconds'] is not None for x in rr),odds_count=sum(x['win_odds'] is not None for x in rr),
                            held_rows=sum(bool(x['holds']) for x in rr)))
    return dict(schema='postrace-staging-v1',records=records,race_summary=summary,
                source_kind='post_race_results',model_ready=False,production_ready=False,
                unavailable=['official_finish','stable_horse_id','verified_popularity','pre_race_as_of'])
