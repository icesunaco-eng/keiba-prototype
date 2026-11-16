"""Offline result odds/popularity candidates; no betting or forecast input."""
import re,unicodedata
def compact(s):return re.sub(r'\s','',s)
def parse_market(s):
    s=compact(s);m=re.fullmatch(r'([0-9０-９．.]+)([①-⑱])',s)
    if not m:return None
    n=unicodedata.normalize('NFKC',m[1])
    if not re.fullmatch(r'\d+\.\d',n):return None
    return {'win_odds':float(n),'popularity':ord(m[2])-ord('①')+1}
def extract(inventory,pages):
    out=[];by_page={p['page_number']:p for p in pages}
    for race in inventory['races']:
        pg=by_page[race['page']];off=race['panel_origin']-18.7
        for pos,row in enumerate(race['rows'],1):
            nameps=[pg['pieces'][i] for i in row['name_source_indices']]
            y=min(p['y'] for p in nameps)
            ps=[(i,p) for i,p in enumerate(pg['pieces']) if 382<=p['x']-off<405 and -.1<=p['y']-y<=1.2]
            raw=''.join(p['t'] for _,p in sorted(ps,key=lambda a:a[1]['x']))
            out.append(dict(page=race['page'],race_no=race['race_no'],umaban=row['umaban'],
                            runner_status=row['runner_status'],table_position=pos,finish=None,
                            custom_market=raw,source_indices=[i for i,_ in ps],
                            x=off+382,y=y-2,width=32,height=7,number_x=off+26))
    return out
def compare(rows):
    checks=[]
    keys=[(r['page'],r['race_no'],r['umaban']) for r in rows]
    for r,key in zip(rows,keys):
        a=parse_market(r['custom_market']);b=parse_market(r.get('pdfkit_market',''))
        # PDFKit exposes opaque Type1 character codes for popularity.
        # Compare only explicit decimal odds; never decode that code by ordinal.
        bm=re.fullmatch(r'(\d+\.\d).',compact(unicodedata.normalize('NFKC',r.get('pdfkit_market',''))))
        pdf_odds=float(bm[1]) if bm else None
        excluded=r['runner_status']=='excluded'
        ok=a is not None and a['win_odds']==pdf_odds and keys.count(key)==1 and not excluded
        number=compact(r.get('pdfkit_umaban',''))
        num_ok=number.isdigit() and int(number)==r['umaban'] and keys.count(key)==1
        checks.append(dict(page=r['page'],race_no=r['race_no'],umaban=r['umaban'],
                           status='excluded' if excluded else 'two_engine_agreement' if ok else 'hold',
                           win_odds=a['win_odds'] if ok else None,popularity_candidate=a['popularity'] if a and not excluded else None,
                           popularity_check='two_engine_agreement' if a and a==b else 'glyph_mapping_only',
                           umaban_check='two_engine_agreement' if num_ok else 'hold',
                           table_position=r['table_position'],finish=None))
    return {'checks':checks,'source_kind':'post_race_results','model_ready':False,'production_ready':False}
