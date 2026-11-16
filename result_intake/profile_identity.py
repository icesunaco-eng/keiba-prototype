"""Same-source saved card/profile identity evidence, not future-entry clearance."""
import re,hashlib,html
from urllib.parse import urlparse,urljoin
from profile_history import horse_key,text,parse
from history_asof import instant

def decode(raw):
    for encoding in ('utf-8','cp932','euc_jp'):
        try:return raw.decode(encoding)
        except UnicodeDecodeError:pass
    raise ValueError('encoding_unknown')

def match(card_raw,card_meta,profile_raw,profile_meta):
    if hashlib.sha256(card_raw).hexdigest()!=card_meta['sha256']:raise ValueError('card_hash')
    u=urlparse(card_meta['url'])
    if u.scheme!='https' or u.hostname not in ('www.jra.go.jp','sp.jra.jp','jra.jp') or u.path!='/JRADB/accessD.html':raise ValueError('official_card_required')
    instant(card_meta['acquired_at'])
    profile=parse(profile_raw,profile_meta);key=profile['horse_key']
    s=decode(profile_raw)
    mobile=re.findall(r'<div\b[^>]*class=["\']bameiSection["\'][^>]*>(.*?)</div>',s,re.S|re.I)
    if mobile:
        names=[text(re.split(r'<br\s*/?>',v,flags=re.I)[0]) for v in mobile]
    else:
        headings=re.findall(r'<h1\b[^>]*>(.*?)</h1>',s,re.S|re.I)
        names=[]
        for h in headings:
            h=re.sub(r'<span\b[^>]*class=["\']name_en["\'][^>]*>.*?</span>','',h,flags=re.S|re.I)
            name=text(h)
            if name.startswith('競走馬情報'):names.append(name.removeprefix('競走馬情報').strip())
    if len(names)!=1 or not names[0]:raise ValueError('profile_name_scope')
    found=[]
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',decode(card_raw),re.S|re.I):
        nums=re.findall(r'<td\b[^>]*class=["\']num["\'][^>]*>(.*?)</td>',row,re.S|re.I)
        if not nums:continue
        links=re.findall(r'<a\b[^>]*href=["\']([^"\']*accessU.html[^"\']*)["\'][^>]*>(.*?)</a>',row,re.S|re.I)
        for href,label in links:
            link=urljoin(card_meta['url'],html.unescape(href))
            if horse_key(link)==key:
                if len(nums)!=1 or not text(nums[0]).isdigit():raise ValueError('card_number')
                found.append((int(text(nums[0])),text(label),link))
    if len(found)!=1:raise ValueError('unique_card_profile_link_required')
    number,name,link=found[0]
    if not 1<=number<=30 or name!=names[0]:raise ValueError('card_profile_identity_conflict')
    return dict(horse_key=key,horse_name=name,card_number=number,profile_link=link,
        status='saved_card_profile_identity_matched',evidence_basis=['official_card_profile_link_key','profile_heading_name'],
        card_sha256=card_meta['sha256'],profile_sha256=profile_meta['sha256'],card_observed_at=card_meta['acquired_at'],profile_observed_at=profile_meta['acquired_at'],
        confirmed_future_entry=False,model_ready=False,production_ready=False)
