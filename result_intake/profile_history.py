"""Small saved JRA profile snapshot parser. No network or prediction execution."""
import re,html,hashlib
from datetime import datetime,date
from urllib.parse import urlparse,parse_qs,unquote,urljoin

def text(s):return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]*>',' ',s))).strip()
def horse_key(url):
    u=urlparse(url)
    if u.scheme!='https' or u.hostname not in ('www.jra.go.jp','jra.jp','sp.jra.jp') or u.path!='/JRADB/accessU.html':raise ValueError('official_profile_required')
    cname=parse_qs(u.query).get('CNAME',[''])[0]
    m=re.fullmatch(r'(?:pw01dud|sj01ude)\d{2}(\d{10})/[A-Fa-f0-9]{2}',cname)
    if not m:raise ValueError('unrecognized_profile_identifier')
    return 'jra-profile:'+m[1]
def parse(raw,metadata):
    if hashlib.sha256(raw).hexdigest()!=metadata['sha256']:raise ValueError('snapshot_hash')
    observed=datetime.fromisoformat(metadata['acquired_at'].replace('Z','+00:00'))
    if observed.utcoffset() is None:raise ValueError('timezone_required')
    key=horse_key(metadata['url'])
    for enc in ('utf-8','cp932','euc_jp'):
        try:s=raw.decode(enc);break
        except UnicodeDecodeError:continue
    else:raise ValueError('encoding_unknown')
    if not all(label in s for label in ('出走レース','頭数','着順','負担重量')):raise ValueError('profile_schema_missing')
    tables=[]
    for table in re.findall(r'<table\b[^>]*>.*?</table>',s,re.S|re.I):
        captions=re.findall(r'<caption\b[^>]*>(.*?)</caption>',table,re.S|re.I)
        if any(text(c)=='出走レース' for c in captions):tables.append(table)
    if len(tables)!=1:raise ValueError('history_table_scope')
    s=tables[0]
    rows=[];seen=set()
    for block in re.findall(r'<tr\b[^>]*>(.*?)</tr>',s,re.S|re.I):
        cells=re.findall(r'<td\b[^>]*>(.*?)</td>',block,re.S|re.I)
        if not cells:continue
        if not any('/JRADB/accessS.html?CNAME=' in cell for cell in cells):raise ValueError('race_link_missing')
        if len(cells)==2:
            a=re.fullmatch(r'(\d{4}年\d{1,2}月\d{1,2}日) (\S+) (.+?) (芝|ダ|障)(\d{3,4}) (\S+) (\d+)頭 (.*)',text(cells[0]))
            b=re.fullmatch(r'(\d+)着 (\d+)人気 (\d+:\d{2}\.\d) (\d+)kg (.+) (\d+\.\d+)',text(cells[1]))
            if not a or not b:raise ValueError('unsupported_mobile_history_row')
            link=re.search(r'<a\b[^>]*href=[\"\'](/JRADB/accessS.html[^\"\']+)[\"\'][^>]*>(.*?)</a>',cells[0],re.S)
            if not link:raise ValueError('race_link_missing')
            cells=[a[1],a[2],link[0],a[4]+a[5],a[6],a[7],b[2],b[1],b[5],b[6],b[4],b[3],'',a[8]]
        # A result-link row is a history candidate, never silently discard it.
        if not re.fullmatch(r'\d{4}年\d{1,2}月\d{1,2}日',text(cells[0])):raise ValueError('invalid_history_date')
        if len(cells)!=14:raise ValueError('changed_history_columns')
        c=list(map(text,cells));d=date(*map(int,re.findall(r'\d+',c[0])))
        if d>observed.date():raise ValueError('future_result_date')
        links=re.findall(r'href=[\"\']([^\"\']+)[\"\']',cells[2]);links=[x for x in links if x.startswith('/JRADB/accessS.html?CNAME=')]
        if len(links)!=1:raise ValueError('race_link_missing')
        race_ref=urljoin(metadata['url'],html.unescape(links[0]))
        if race_ref in seen:raise ValueError('duplicate_history')
        seen.add(race_ref)
        if not c[5].isdigit() or not 1<=int(c[5])<=30:raise ValueError('field_size')
        popularity=int(c[6]) if c[6].isdigit() else None
        if popularity is not None and not 1<=popularity<=int(c[5]):raise ValueError('popularity')
        finish=int(c[7]) if c[7].isdigit() else None
        if finish is not None and not 1<=finish<=int(c[5]):raise ValueError('finish')
        distance=re.fullmatch(r'(芝|ダ|障)(\d{3,4})',c[3])
        rows.append(dict(horse_key=key,event_date=d.isoformat(),venue=c[1],race_name=c[2],race_ref=race_ref,
          surface=distance[1] if distance else None,distance_m=int(distance[2]) if distance else None,
          going=c[4],field_size=int(c[5]),popularity=popularity,
          finish=finish,finish_raw=c[7],jockey=c[8],weight_raw=c[9],body_weight_raw=c[10],time_raw=c[11],
          observed_at=metadata['acquired_at'],published_at=None,start_at=None,
          source_sha256=metadata['sha256'],source_url=metadata['url'],source_kind='post_race_history_snapshot'))
    if not rows:raise ValueError('no_history_rows')
    return dict(horse_key=key,rows=rows,source_kind='post_race_history_snapshot',observed_at=metadata['acquired_at'],
                model_ready=False,production_ready=False,needs=['identity_crosscheck','target_entry_snapshot','source_time_review'])
def batch(saved):
    reports=[parse(raw,meta) for raw,meta in saved]
    keys=[x['horse_key'] for x in reports]
    if len(keys)!=len(set(keys)):raise ValueError('duplicate_profile_snapshot_requires_revision_policy')
    return reports
