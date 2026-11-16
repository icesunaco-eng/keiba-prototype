"""Extract all official profile links from one already saved race card; no network."""
import re,hashlib,html
from urllib.parse import urlparse,urljoin
from profile_history import horse_key,text
from profile_identity import decode
from history_asof import instant

def extract(raw,meta):
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('card_hash')
    u=urlparse(meta['url'])
    if u.scheme!='https' or u.hostname not in ('www.jra.go.jp','sp.jra.jp','jra.jp') or u.path!='/JRADB/accessD.html':raise ValueError('official_card_required')
    instant(meta['acquired_at']);records=[];numbers=set();ids=set()
    for block in re.findall(r'<tr\b[^>]*>(.*?)</tr>',decode(raw),re.S|re.I):
        nums=re.findall(r'<td\b[^>]*class=["\']num["\'][^>]*>(.*?)</td>',block,re.S|re.I)
        if not nums:continue
        links=re.findall(r'<a\b[^>]*href=["\']([^"\']*accessU.html[^"\']*)["\'][^>]*>(.*?)</a>',block,re.S|re.I)
        if len(nums)!=1 or len(links)!=1 or not text(nums[0]).isdigit():raise ValueError('card_row_schema')
        number=int(text(nums[0]));url=urljoin(meta['url'],html.unescape(links[0][0]));key=horse_key(url);name=text(links[0][1])
        if number in numbers or key in ids or not name or not 1<=number<=30:raise ValueError('duplicate_or_invalid_identity')
        numbers.add(number);ids.add(key)
        records.append(dict(horse_number=number,horse_name=name,horse_key=key,profile_url=url,card_source_sha256=meta['sha256'],observed_at=meta['acquired_at']))
    if not records or sorted(numbers)!=list(range(1,len(records)+1)):raise ValueError('card_number_coverage')
    return dict(schema='saved-card-profile-links-v1',records=sorted(records,key=lambda r:r['horse_number']),source_url=meta['url'],
                source_sha256=meta['sha256'],observed_at=meta['acquired_at'],profile_pages_fetched=False,
                independently_verified=False,model_ready=False,confirmed_future_entry=False)
