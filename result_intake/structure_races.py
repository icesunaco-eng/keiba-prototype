"""Offline JRA landscape-layout candidate inventory. Never prediction input.
Only inspected two-panel layout is supported; no arbitrary-PDF guarantee.
"""
import re, math
from datetime import date
NORM=str.maketrans({'⾺':'馬','⾛':'走','⽇':'日','⽉':'月','，':',','（':'(','）':')'})
def norm(s): return s.translate(NORM)
def text(ps): return norm(''.join(p['t'] for p in sorted(ps,key=lambda p:p['x'])))
def structure_pages(pages):
    races=[]; holds=[]
    for page in pages:
        pn=page.get('page_number'); pieces=page.get('pieces')
        if not isinstance(pieces,list) or not pieces or any(not isinstance(p,dict) or not isinstance(p.get('t'),str) or any(type(p.get(k)) not in (float,int) or not math.isfinite(p[k]) for k in ('x','y')) for p in pieces):
            holds.append({'page':pn,'reason':'invalid_fragments'});continue
        found=0
        for origin in (18.7,429.8):
            starts=[p['x'] for p in pieces if abs(p['x']-origin)<1 and p['y']>450 and re.fullmatch(r'3(?:\d{4})?',p['t'])]
            if len(set(starts))==1: origin=starts[0]
            panel=[dict(p,x=p['x']-origin+18.7,source_index=i) for i,p in enumerate(pieces) if origin-1<=p['x']<origin+410]
            anchors=[p for p in panel if abs(p['x']-18.7)<.2 and p['y']>450]
            candidates=[]
            for a in anchors:
                label=text([p for p in panel if 18.5<=p['x']<23 and abs(p['y']-a['y'])<.2])
                header=text([p for p in panel if 23<=p['x']<245 and abs(p['y']-a['y'])<=3])
                md=re.search(r'(\d{1,2})月(\d{1,2})日',header)
                yv=re.search(r'(\d{4})年(\d+)(東京|中山|京都|阪神|中京|新潟|福島|小倉|札幌|函館)',header)
                nr=re.search(r'第(\d+)日第(\d+)競走',header)
                if re.fullmatch(r'3\d{4}',label) and md and yv and nr:
                    key=(label,header)
                    if key not in [x[0] for x in candidates]: candidates.append((key,a,md,yv,nr))
            if len(candidates)!=1:
                if panel: holds.append({'page':pn,'panel_origin':origin,'reason':'missing_or_ambiguous_header'})
                continue
            _,a,md,yv,nr=candidates[0];found+=1
            issues=[]
            try: dt=date(int(yv[1]),int(md[1]),int(md[2])).isoformat()
            except ValueError: dt=None;issues.append('invalid_date')
            race_no=int(nr[2]);
            if not 1<=race_no<=12: issues.append('invalid_race_number')
            fs=[]
            # Only combine same-baseline fragments, not arbitrary full-page text.
            for y in sorted({p['y'] for p in panel}):
                line=text([p for p in panel if abs(p['y']-y)<.1])
                fs.extend(int(v) for v in re.findall(r'\((\d+)頭\)',line))
            fs=set(fs)
            field_size=next(iter(fs)) if len(fs)==1 else None
            if field_size is None: issues.append('field_size_unresolved')
            rowys=sorted({p['y'] for p in panel if abs(p['x']-21)<.3 and re.fullmatch('[1-8]',p['t'])},reverse=True)
            rows=[]
            for y in rowys:
                rp=[p for p in panel if abs(p['y']-y)<.3]
                w=text([p for p in rp if abs(p['x']-21)<.3]); u=text([p for p in rp if 21.5<p['x']<23])
                name=text([p for p in rp if 49<=p['x']<121])
                if not re.fullmatch('[1-8]',w) or not re.fullmatch(r'\d{1,2}',u) or not name: issues.append('incomplete_row');continue
                rows.append({'waku':int(w),'umaban':int(u),'name_candidate':name,'horse_id':None,'finish':None,'source_indices':[p['source_index'] for p in rp]})
            nums=[r['umaban'] for r in rows]
            if len(nums)!=len(set(nums)): issues.append('duplicate_runner')
            if field_size!=len(rows): issues.append('row_count_mismatch')
            if any(not 1<=x<=18 for x in nums): issues.append('invalid_runner_number')
            # Unknown glyph may be outside results but remains unresolved until located/reviewed.
            if page.get('unknown_glyph_occurrences',0): issues.append('page_unknown_glyphs')
            if any('\ufffd' in p['t'] or re.search(r'\{(?:c|cid)\d+\}',p['t']) for p in panel): issues.append('unresolved_panel_glyphs')
            races.append({'page':pn,'panel_origin':origin,'race_label':candidates[0][0][0],'date':dt,'venue':yv[3],'kai':int(yv[2]),'nichi':int(nr[1]),'race_no':race_no,'field_size':field_size,'rows':rows,'status':'hold' if issues else 'candidate','issues':sorted(set(issues)),'text_verified':False})
    keys=[(r['date'],r['venue'],r['kai'],r['nichi'],r['race_no']) for r in races]
    for i,r in enumerate(races):
        if keys.count(keys[i])>1: r['status']='hold';r['issues'].append('duplicate_race_identity')
    return {'schema':'race-candidate-inventory-v1','races':races,'page_holds':holds,'model_ready':False,'prediction_created':False,'source_kind':'post_race_results','announced_at':None}
