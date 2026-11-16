"""Stage an existing normalized result without inventing cross-race IDs/times."""
from datetime import date,datetime
import hashlib,json

def stage(record, meta):
    if type(record) is not dict or type(meta) is not dict: raise ValueError('objects required')
    for k in ('date','venue','kai','nichi','race_no','field_size','rows'):
        if k not in record: raise ValueError('missing race metadata')
    day=date.fromisoformat(record['date'])
    if type(record['venue']) is not str or not record['venue'].strip(): raise ValueError('venue required')
    for k in ('kai','nichi','race_no','field_size'):
        if type(record[k]) is not int or record[k]<1: raise ValueError('invalid race number')
    if record.get('issues') or record.get('holds'): raise ValueError('unresolved source issues')
    n=record['field_size'];rows=record['rows']
    if type(rows) is not list or len(rows)!=n: raise ValueError('incomplete result rows')
    numbers=[r.get('umaban') for r in rows];orders=[r.get('order_by_row') for r in rows]
    if any(type(x) is not int for x in numbers+orders) or set(numbers)!=set(range(1,n+1)) or set(orders)!=set(range(1,n+1)):raise ValueError('invalid row identity')
    sha=meta.get('sha256');url=meta.get('url')
    if type(sha) is not str or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): raise ValueError('source sha required')
    if type(url) is not str or not url.startswith('https://'): raise ValueError('source reference required')
    fetched=datetime.fromisoformat(meta['fetched_utc'].replace('Z','+00:00'))
    if fetched.utcoffset() is None or fetched.date()<day: raise ValueError('invalid observation time')
    race=f"{day.isoformat()}/{record['venue']}/{record['kai']}/{record['nichi']}/{record['race_no']}"
    entries=[]
    for row in sorted(rows,key=lambda r:r['umaban']):
        name=row.get('name')
        if type(name) is not str or not name.strip(): raise ValueError('missing display name')
        entries.append({'race_entry_key':race+'/'+str(row['umaban']), 'horse_id':None,
                        'display_name':name,'horse_number':row['umaban'],
                        'finish_candidate':row['order_by_row'],'finish_basis':'normalized_row_order',
                        'source_ref':url,'source_hash':sha,'observed_at':fetched.isoformat()})
    return {'schema':'result-intake-stage-v1','race_key':race,'race_date':day.isoformat(),
            'scheduled_post_label':record.get('post_label'),'actual_start':None,
            'announced_at':record.get('announced_at'),'observed_at':fetched.isoformat(),
            'entries':entries,'normalized_input_hash':hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            'state':'staged_not_model_input','history_rows':[],
            'blockers':['stable_horse_identity_unresolved','availability_time_not_validated','actual_start_not_validated','row_order_finish_not_promoted'],
            'model_ready':False,'prediction_created':False,'order_created':False}
