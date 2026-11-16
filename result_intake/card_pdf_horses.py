"""Candidate horse records from meeting PDF text; not verified identities."""
import re
from datetime import date
from card_pdf_inventory import inventory

def extract(pdf_raw,text,metadata,expected_races=12):
    inv=inventory(pdf_raw,text,metadata,expected_races);records=[];holds=[]
    for race in inv['races']:
        versions=[]
        for begin,end in race['repeated_header_spans']:
            section=text[begin:end]
            # Explicit horse-number header followed by a sex/age English marker.
            starts=list(re.finditer(r'(?m)^(?:白|黒|赤|青|黄|緑|橙|桃)?[ \t]*(\d{1,2})\n(?:[^\n]*\n){1,4}?([CFGMH]\d{1,2})\n',section))
            rows=[]
            for i,m in enumerate(starts):
                stop=starts[i+1].start() if i+1<len(starts) else len(section)
                block=section[m.start():stop]
                births=list(re.finditer(r'(\d{4})．(\d{1,2})．(\d{1,2})生',block))
                issues=[];name=None;birth=None
                if len(births)!=1:issues.append('birthdate_ambiguous')
                else:
                    birth=date(*map(int,births[0].groups())).isoformat()
                    head=block[:births[0].start()]
                    # Keep raw text; unknown encoded decoration is not transliterated.
                    candidates=re.findall(r'(?m)^([ァ-ヶー・ヴ][ァ-ヶー・ヴ \t]*)[ \t]+(?=[一-龯A-Za-zＣＴＡ])',head)
                    if len(candidates)==1:name=re.sub(r'[ \t]','',candidates[0])
                    else:issues.append('horse_name_ambiguous')
                rows.append(dict(race_number=race['race_number'],horse_number=int(m[1]),horse_name_candidate=name,birthdate_candidate=birth,
                    sex_age_raw=m[2],text_span=[begin+m.start(),begin+stop],holds=issues))
            if [r['horse_number'] for r in rows]!=list(range(1,race['listed_count']+1)):holds.append(dict(race_number=race['race_number'],reason='horse_number_coverage',found=[r['horse_number'] for r in rows],expected=race['listed_count']))
            versions.append(rows)
        rows=versions[0]
        signature=lambda rs:[{k:r[k] for k in ('horse_number','horse_name_candidate','birthdate_candidate','sex_age_raw','holds')} for r in rs]
        if any(signature(v)!=signature(rows) for v in versions[1:]):holds.append(dict(race_number=race['race_number'],reason='repeated_horse_content_conflict'))
        for row in rows:
            row.update(source_sha256=metadata['sha256'],observed_at=metadata['acquired_at'],status='candidate_requires_independent_validation')
        records.extend(rows)
    return dict(schema='meeting-card-horses-v1',records=records,race_holds=holds,listed_count=inv['total_listed'],source_sha256=metadata['sha256'],
                confirmed_future_entry=False,model_ready=False,production_ready=False)
