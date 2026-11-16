"""Post-race podium candidates grounded in explicit payout combinations.
Does not infer fourth and lower ranks or promote candidates to production.
"""
import re,unicodedata

def extract(inventory,pages,source_sha256):
    if not re.fullmatch('[0-9a-f]{64}',source_sha256):raise ValueError('source_hash')
    out=[];pgs={p['page_number']:p for p in pages}
    for race in inventory['races']:
        pg=pgs[race['page']];offset=race['panel_origin']-18.7
        lowest=min(pg['pieces'][i]['y'] for row in race['rows'] for i in row['name_source_indices'])
        evidence={};reasons=[]
        for label,length in [('単勝',1),('馬単',2),('3連単',3)]:
            labels=[(i,p) for i,p in enumerate(pg['pieces']) if 18<=p['x']-offset<410 and p['y']<lowest and unicodedata.normalize('NFKC',p['t'])==label]
            if len(labels)!=1:reasons.append('label_missing_or_ambiguous:'+label);continue
            li,lab=labels[0]
            fragments=[(i,p) for i,p in enumerate(pg['pieces']) if 25<=p['x']-lab['x']<=45 and abs(p['y']-lab['y'])<=1 and re.fullmatch('[①-⑱]+',p['t'])]
            if len(fragments)!=1 or len(fragments[0][1]['t'])!=length:
                reasons.append('combination_missing_or_ambiguous:'+label);continue
            index,piece=fragments[0];numbers=[ord(c)-ord('①')+1 for c in piece['t']]
            if len(set(numbers))!=length:reasons.append('duplicate_combination:'+label)
            evidence[label]=dict(numbers=numbers,raw=piece['t'],source_indices=[li,index],font=piece['f'],x=piece['x'],y=piece['y'])
        active=[r for r in race['rows'] if r['runner_status']!='excluded']
        top=[r['umaban'] for r in active[:3]]
        if not reasons:
            if len(top)!=3 or evidence['単勝']['numbers']!=top[:1] or evidence['馬単']['numbers']!=top[:2] or evidence['3連単']['numbers']!=top:
                reasons.append('payout_table_order_disagreement')
        out.append(dict(page=race['page'],race_no=race['race_no'],source_sha256=source_sha256,evidence=evidence,
                        podium_candidate=top if not reasons else None,status='payout_and_table_agreement' if not reasons else 'hold',
                        reasons=reasons,official_finish_verified=False))
    return dict(races=out,source_kind='post_race_results',model_ready=False,production_ready=False)
