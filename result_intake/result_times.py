"""Post-race time candidates; ditto marks are deliberately not resolved."""
import re,unicodedata
def normalize(s):return re.sub(r'\s','',unicodedata.normalize('NFKC',s))
def parse_time(value):
    t=normalize(value)
    if t=='〃':return {'seconds':None,'status':'ditto_pending','raw':value}
    m=re.fullmatch(r'(\d{1,2}):(\d{2})\.(\d)',t)
    if not m or int(m[2])>=60:return {'seconds':None,'status':'hold','raw':value}
    return {'seconds':int(m[1])*60+int(m[2])+int(m[3])/10,'status':'candidate','raw':value}
def extract(inventory,pages):
    by_page={p['page_number']:p for p in pages};out=[]
    for race in inventory['races']:
        page=by_page[race['page']];offset=race['panel_origin']-18.7
        for row in race['rows']:
            namepieces=[page['pieces'][i] for i in row['name_source_indices']]
            y=min(p['y'] for p in namepieces)
            # Result-time baselines are ~0.4..0.8 pt above name baseline.
            pieces=[(i,p) for i,p in enumerate(page['pieces']) if (330<=p['x']-offset<350 or (310<=p['x']-offset<330 and p['t']=='〃')) and -.1<=p['y']-y<=1.2]
            pieces.sort(key=lambda ip:ip[1]['x'])
            raw=''.join(p['t'] for _,p in pieces)
            item=dict(page=race['page'],race_no=race['race_no'],umaban=row['umaban'],
                      runner_status=row['runner_status'],custom_time=raw,
                      time_source_indices=[i for i,_ in pieces],
                      x=offset+335,y=y-2,width=22,height=7)
            item.update(parse_time(raw))
            if row['runner_status']=='excluded':item.update(seconds=None,status='excluded')
            out.append(item)
    return out
def compare(rows):
    out=[]
    keys=[(r['page'],r['race_no'],r['umaban']) for r in rows]
    for row in rows:
        parsed=parse_time(row['custom_time'])
        other=row.get('pdfkit_time','')
        status='two_engine_agreement' if parsed['status']=='candidate' and normalize(row['custom_time'])==normalize(other) else 'hold'
        if row['runner_status']=='excluded':status='excluded'
        duplicate=keys.count((row['page'],row['race_no'],row['umaban']))!=1
        if duplicate:status='hold'
        out.append(dict(page=row['page'],race_no=row['race_no'],umaban=row['umaban'],
                        status=status,seconds=parsed['seconds'] if status=='two_engine_agreement' else None,
                        reason=None if status in ('two_engine_agreement','excluded') else ('duplicate_identity' if duplicate else 'engine_disagreement' if parsed['status']=='candidate' else parsed['status'])))
    return {'checks':out,'source_kind':'post_race_results','model_ready':False,'production_ready':False}
