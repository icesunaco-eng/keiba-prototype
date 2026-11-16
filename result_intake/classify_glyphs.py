"""Exact embedded-font classified unresolved glyphs; no deletion or guessing."""
import re,hashlib
def classify_page(module,objs,page_body,pieces,registry):
    rid=module.ref(page_body,b'Resources');res=objs[rid] if rid else page_body
    fm=re.search(rb'/Font\s*<<(.*?)>>',res,re.S)
    if not fm:
        fid=module.ref(res,b'Font');fm=re.search(rb'<<(.*)>>',objs.get(fid,b''),re.S)
    lookup={}
    if fm:
        for name,num in re.findall(rb'/([A-Za-z0-9_.+-]+)\s+(\d+)\s+0\s+R',fm.group(1)):
            fb=objs[int(num)];fd=module.ref(fb,b'FontDescriptor')
            ff=module.ref(objs[fd],b'FontFile3') if fd else None
            if ff:
                data=module.stream_of(objs[ff])
                if data is not None:lookup[name.decode()]=registry.get(hashlib.sha256(data).hexdigest(),{})
    out=[]
    for i,p in enumerate(pieces):
        for token in re.findall(r'\{c\d+\}',p['t']):
            out.append({'source_index':i,'token':token,'category':lookup.get(p['f'],{}).get(token,'unclassified'),'x':p['x'],'y':p['y']})
    return out
