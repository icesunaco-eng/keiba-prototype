"""Link saved official HTML IDs to same-event PDF candidates, no acquisition."""
import re
from urllib.parse import urlparse,parse_qs
from profile_history import horse_key
from history_asof import instant
VENUES={'05':'東京','06':'中山','08':'京都','09':'阪神','07':'中京','04':'新潟','03':'福島','10':'小倉','01':'札幌','02':'函館'}
def join(card,inventory,links):
    url=urlparse(links['source_url'])
    if url.scheme!='https' or url.hostname not in ('www.jra.go.jp','sp.jra.jp','jra.jp') or url.path!='/JRADB/accessD.html':raise ValueError('official_card_url')
    observed=instant(links['observed_at'])
    keys=set()
    for item in links['records']:
        if item['card_source_sha256']!=links['source_sha256'] or instant(item['observed_at'])!=observed:raise ValueError('html_row_provenance')
        if horse_key(item['profile_url'])!=item['horse_key'] or item['horse_key'] in keys:raise ValueError('profile_key_integrity')
        keys.add(item['horse_key'])
    cname=parse_qs(url.query).get('CNAME',[''])[0]
    m=re.fullmatch(r'(?:sw|pw)01ddd\d{2}(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})(\d{8})/[0-9A-Fa-f]{2}',cname)
    if not m:raise ValueError('unsupported_card_scope')
    venue,year,meeting,day,race,event=m.groups();event=f'{event[:4]}-{event[4:6]}-{event[6:]}'
    rs=[r for r in inventory['races'] if r['race_number']==int(race)]
    if len(rs)!=1:raise ValueError('race_scope')
    r=rs[0]
    if inventory['event_date']!=event or r['venue']!=VENUES.get(venue) or r['meeting']!=int(meeting) or r['meeting_day']!=int(day) or event[:4]!=year:raise ValueError('event_scope')
    if card['source_sha256']!=r['source_sha256']:raise ValueError('pdf_source')
    if any(h['race_number']==int(race) for h in card['race_holds']):raise ValueError('race_hold')
    left={v['horse_number']:v for v in card['records'] if v['race_number']==int(race)}
    right={v['horse_number']:v for v in links['records']}
    expected=set(range(1,r['listed_count']+1))
    if set(left)!=expected or set(right)!=expected or len(right)!=len(links['records']) or len(left)!=sum(v['race_number']==int(race) for v in card['records']):raise ValueError('coverage')
    out=[]
    for n in sorted(expected):
        a,b=left[n],right[n]
        if a['source_sha256']!=card['source_sha256'] or instant(a['observed_at'])!=instant(r['observed_at']):raise ValueError('pdf_row_provenance')
        if a['holds'] or a['horse_name_candidate']!=b['horse_name']:raise ValueError('identity_conflict')
        out.append(dict(race_number=int(race),horse_number=n,horse_name=a['horse_name_candidate'],horse_key=b['horse_key'],
            event_date=event,venue=r['venue'],pdf_source_sha256=card['source_sha256'],html_source_sha256=links['source_sha256'],
            pdf_observed_at=a['observed_at'],html_observed_at=links['observed_at'],status='same_event_official_id_link_candidate'))
    return dict(records=out,linked_count=len(out),unlinked_count=card['listed_count']-len(out),future_entry_confirmed=False,model_ready=False)
