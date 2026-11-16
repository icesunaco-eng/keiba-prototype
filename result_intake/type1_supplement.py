"""Apply visually checked glyph mappings to EXACT embedded CFF bytes only.
Unmapped glyphs retain their original placeholders. No font-name inference.
"""
import re,hashlib
def install(module,registry):
    original=module.page_fonts
    def mapped(objs,page_body):
        fonts=original(objs,page_body)
        rid=module.ref(page_body,b'Resources')
        res=objs[rid] if rid else page_body
        fm=re.search(rb'/Font\s*<<(.*?)>>',res,re.S)
        if not fm:
            fid=module.ref(res,b'Font')
            fm=re.search(rb'<<(.*)>>',objs.get(fid,b''),re.S)
        if not fm:return fonts
        for name,num in re.findall(rb'/([A-Za-z0-9_.+-]+)\s+(\d+)\s+0\s+R',fm.group(1)):
            fb=objs[int(num)];fd=module.ref(fb,b'FontDescriptor')
            ff=module.ref(objs[fd],b'FontFile3') if fd else None
            if not ff:continue
            raw=module.stream_of(objs[ff])
            if raw is None:continue
            entries=registry.get(hashlib.sha256(raw).hexdigest())
            key=name.decode()
            if not entries or key not in fonts:continue
            cm,two=fonts[key]
            if two:continue
            fonts[key]=({code:entries.get(value,value) for code,value in cm.items()},two)
        return fonts
    module.page_fonts=mapped
