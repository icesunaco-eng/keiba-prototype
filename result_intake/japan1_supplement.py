"""Two CID mappings verified against installed Adobe-Japan1-UCS2 v10.002.
Not a glyph-name guess. Install only in the existing Japan1 CID fallback.
"""
MAPPINGS={7961:'芦',8267:'辻'}
SOURCE_SHA256='6a9693361647a37996312cc57071bb79f8c06411207be7c730a83fda1254cd82'
def install(module):
    original=module.aj1_map
    def mapped():
        values=dict(original())
        for cid,value in MAPPINGS.items():
            if cid in values and values[cid]!=value:
                raise ValueError('conflicting_cid_mapping')
            values[cid]=value
        return values
    module.aj1_map=mapped
