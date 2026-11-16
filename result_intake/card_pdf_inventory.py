"""Offline meeting-card text inventory. Extractor text remains unverified evidence."""
import re,hashlib
from urllib.parse import urlparse
from history_asof import instant

def inventory(pdf_raw,text,metadata,expected_races=12):
    if hashlib.sha256(pdf_raw).hexdigest()!=metadata['sha256']:raise ValueError('pdf_hash')
    if not pdf_raw.startswith(b'%PDF-'):raise ValueError('pdf_required')
    u=urlparse(metadata['url'])
    if u.scheme!='https' or u.hostname!='www.jra.go.jp' or not re.fullmatch(r'/keiba/rpdf/pdf/\d{8}-\d{2}[a-z]+\d{2}\.pdf',u.path):raise ValueError('official_meeting_card_url')
    observed=instant(metadata['acquired_at'])
    from datetime import datetime
    day=datetime.strptime(u.path.split('/')[-1][:8],'%Y%m%d').date()
    pattern=r'(?m)^(\d{4})年(\d+)(東京|京都|阪神|中山|中京|新潟|福島|小倉|札幌|函館)(\d+) '
    headers=list(re.finditer(pattern,text));races=[]
    for i,h in enumerate(headers):
        end=headers[i+1].start() if i+1<len(headers) else len(text)
        section=text[h.start():end]
        number=re.findall(r'(?m)^(\d{1,2})R(?:[ \t]+〔[^\n]*〕)?[ \t]*$',section)
        count=re.findall(r'（(\d{1,2})頭）',section)
        distance=re.findall(r'(?m)^([\d，,]+)[^\n]*（\d{1,2}頭）',section)
        times=re.findall(r'Time\s*(\d{1,2})時(\d{2})分',section)
        if len(number)!=1 or len(count)!=1 or len(distance)!=1 or len(times)!=1:raise ValueError('ambiguous_race_header')
        n=int(number[0]);c=int(count[0]);dist=int(distance[0].replace('，','').replace(',',''))
        if not 1<=n<=12 or not 1<=c<=30 or not 100<=dist<=9999:raise ValueError('race_header_range')
        if int(h[1])!=day.year:raise ValueError('meeting_year')
        hour,minute=map(int,times[0])
        if not 0<=hour<=23 or not 0<=minute<=59:raise ValueError('post_time')
        races.append(dict(race_number=n,venue=h[3],meeting=int(h[2]),meeting_day=int(h[4]),listed_count=c,distance_m=dist,
          post_time_candidate=f'{day.isoformat()}T{hour:02}:{minute:02}:00+09:00',text_span=[h.start(),end],
          source_sha256=metadata['sha256'],source_url=metadata['url'],observed_at=metadata['acquired_at'],status='header_candidates_require_independent_check'))
    unique={}
    for row in races:
        n=row['race_number']
        if n in unique:
            keys=('venue','meeting','meeting_day','listed_count','distance_m','post_time_candidate')
            if any(row[k]!=unique[n][k] for k in keys):raise ValueError('conflicting_repeated_header')
            unique[n]['repeated_header_spans'].append(row['text_span'])
        else:
            unique[n]=dict(row,repeated_header_spans=[row['text_span']])
    races=list(unique.values())
    if len(races)!=expected_races or sorted(r['race_number'] for r in races)!=list(range(1,expected_races+1)):raise ValueError('race_coverage')
    if len({(r['venue'],r['meeting'],r['meeting_day']) for r in races})!=1:raise ValueError('mixed_meeting')
    return dict(schema='meeting-card-inventory-v1',event_date=day.isoformat(),races=sorted(races,key=lambda r:r['race_number']),total_listed=sum(r['listed_count'] for r in races),
        extracted_text_sha256=hashlib.sha256(text.encode()).hexdigest(),extractor='supplied_text_unverified',
        model_ready=False,production_ready=False,confirmed_future_entry=False)
