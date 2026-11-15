"""標準ライブラリだけの最小PDFテキスト抽出（ToUnicode CMap を使う）。対象PDFは引数で受け取り、読むだけ。
出力：ページごとのテキスト断片（表示位置 x, y つき）を JSON で書き出す。推測で文字を補わない（未対応のグリフは U+FFFD）。"""
import re, sys, json, zlib

def objects(data):
    out = {}
    for m in re.finditer(rb'(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj', data, re.S):
        out[int(m.group(1))] = m.group(3)
    return out

def stream_of(body):
    m = re.search(rb'stream(?:\r\n|\r|\n)(.*?)(?:\r\n|\r|\n)?endstream', body, re.S)
    if not m:
        return None
    raw = m.group(1)
    if b'/FlateDecode' in body.split(b'stream', 1)[0]:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompressobj().decompress(raw)
    return raw

def parse_cmap(text):
    cmap = {}
    for blk in re.findall(rb'beginbfchar(.*?)endbfchar', text, re.S):
        for a, b in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            cmap[int(a, 16)] = bytes.fromhex(b.decode()).decode('utf-16-be', 'replace')
    for blk in re.findall(rb'beginbfrange(.*?)endbfrange', text, re.S):
        for m in re.finditer(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])', blk):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            dst = m.group(3)
            if dst.startswith(b'['):
                items = re.findall(rb'<([0-9A-Fa-f]+)>', dst)
                for i, it in enumerate(items):
                    cmap[lo + i] = bytes.fromhex(it.decode()).decode('utf-16-be', 'replace')
            else:
                base = int(dst[1:-1], 16)
                width = len(dst[1:-1]) // 2
                for c in range(lo, hi + 1):
                    cmap[c] = (base + c - lo).to_bytes(width, 'big').decode('utf-16-be', 'replace')
    return cmap

_AJ1 = None
UNKNOWN = {}
def aj1_map():
    """Adobe-Japan1 の CID→Unicode。Adobe 公開の UniJIS-UTF32-H（Unicode→CID）を逆引きする。1つの CID に複数の Unicode が
    対応するときは、最も小さい符号位置を採る（例：数字は U+0030〜）。表の所在は環境変数 AJ1_CMAP。"""
    global _AJ1
    if _AJ1 is None:
        import os
        txt = open(os.environ.get('AJ1_CMAP', 'ref/UniJIS-UTF32-H'), 'rb').read()
        inv = {}
        for blk in re.findall(rb'begincidchar(.*?)endcidchar', txt, re.S):
            for u, c in re.findall(rb'<([0-9a-fA-F]+)>\s+(\d+)', blk):
                u, c = int(u, 16), int(c)
                if c not in inv or u < inv[c]:
                    inv[c] = u
        for blk in re.findall(rb'begincidrange(.*?)endcidrange', txt, re.S):
            for lo, hi, c in re.findall(rb'<([0-9a-fA-F]+)>\s+<([0-9a-fA-F]+)>\s+(\d+)', blk):
                lo, hi, c = int(lo, 16), int(hi, 16), int(c)
                for k in range(hi - lo + 1):
                    if c + k not in inv or lo + k < inv[c + k]:
                        inv[c + k] = lo + k
        _AJ1 = {c: chr(u) for c, u in inv.items()}
        # 補助：Unicode CMap に載らない数字の字形 CID 9872〜9881 を 0〜9 とする。根拠は原本の見出しの既知事実との一致
        # （年 2026・11月14日・第5回東京・第3日。logs/cid-digit-evidence.json）。それ以外の未対応 CID は推測しない
        for d in range(10):
            _AJ1.setdefault(9872 + d, str(d))
    return _AJ1

def ref(body, key):
    m = re.search(rb'/' + key + rb'\s+(\d+)\s+0\s+R', body)
    return int(m.group(1)) if m else None

def page_fonts(objs, page_body):
    fonts = {}
    res = page_body
    rid = ref(page_body, b'Resources')
    if rid:
        res = objs[rid]
    m = re.search(rb'/Font\s*<<(.*?)>>', res, re.S)
    if not m:
        fid = ref(res, b'Font')
        fd = objs.get(fid, b'') if fid else b''
        m = re.search(rb'<<(.*)>>', fd, re.S)
    if not m:
        return fonts
    for name, num in re.findall(rb'/([A-Za-z0-9_.+-]+)\s+(\d+)\s+0\s+R', m.group(1)):
        fb = objs.get(int(num), b'')
        tu = ref(fb, b'ToUnicode')
        cm = parse_cmap(stream_of(objs[tu]) or b'') if tu else {}
        two = b'/Type0' in fb
        if not cm and two and b'Identity-H' in fb:
            df = re.findall(rb'/DescendantFonts\s*\[(\d+)', fb)
            if df and b'Japan1' in objs.get(int(df[0]), b''):
                cm = aj1_map()   # Identity-H なので文字コード＝CID
        if not cm and not two:
            # ToUnicode のない単純フォント：/Differences の字形名を {名前} として残す。字形名から文字は推測しない
            e = ref(fb, b'Encoding')
            eb = objs.get(e, b'') if e else fb
            dm = re.search(rb'/Differences\s*\[(.*?)\]', eb, re.S)
            if dm:
                code = 0
                for tok in re.findall(rb'\d+|/[^\s/\[\]]+', dm.group(1)):
                    if tok[:1] == b'/':
                        g = tok[1:].decode()
                        cm[code] = '{' + g + '}'
                        code += 1
                    else:
                        code = int(tok)
        fonts[name.decode()] = (cm, two)
    return fonts

def decode(s, font):
    cm, two = font
    out = []
    step = 2 if two else 1
    for i in range(0, len(s) - step + 1, step):
        c = int.from_bytes(s[i:i + step], 'big')
        out.append(cm.get(c, '�') if cm else chr(c))
        if cm and c not in cm:
            UNKNOWN[c] = UNKNOWN.get(c, 0) + 1
    return ''.join(out)

def lit(b):
    r, i = bytearray(), 0
    while i < len(b):
        c = b[i]
        if c == 0x5C and i + 1 < len(b):
            n = b[i + 1]
            esc = {ord('n'): 10, ord('r'): 13, ord('t'): 9, ord('b'): 8, ord('f'): 12}
            if n in esc:
                r.append(esc[n]); i += 2
            elif 48 <= n <= 55:
                j = i + 1; v = 0
                while j < len(b) and j < i + 4 and 48 <= b[j] <= 55:
                    v = v * 8 + b[j] - 48; j += 1
                r.append(v & 255); i = j
            else:
                r.append(n); i += 2
        else:
            r.append(c); i += 1
    return bytes(r)

TOK = re.compile(rb'\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[-+]?\d*\.?\d+|[A-Za-z\'"*]+')

def run(content, fonts):
    pieces, stack, font, fname, x, y, tm = [], [], None, None, 0.0, 0.0, [1, 0, 0, 1, 0, 0]
    lx = ly = 0.0
    arr = None
    for t in TOK.findall(content):
        if t == b'[':
            arr = []; continue
        if t == b']':
            stack.append(arr); arr = None; continue
        if arr is not None:
            arr.append(t); continue
        if t in (b'BT',):
            tm = [1, 0, 0, 1, 0, 0]; lx = ly = 0.0; stack = []; continue
        if t == b'Tf':
            fname = stack[-2][1:].decode() if len(stack) >= 2 else None
            font = fonts.get(fname)
        elif t == b'Tm' and len(stack) >= 6:
            tm = [float(v) for v in stack[-6:]]; lx, ly = tm[4], tm[5]
        elif t in (b'Td', b'TD') and len(stack) >= 2:
            lx += float(stack[-2]); ly += float(stack[-1])
        elif t in (b'Tj', b"'", b'"', b'TJ') and font is not None and stack:
            v = stack[-1]
            items = v if isinstance(v, list) else [v]
            txt = ''
            for it in items:
                if isinstance(it, bytes) and it.startswith(b'('):
                    txt += decode(lit(it[1:-1]), font)
                elif isinstance(it, bytes) and it.startswith(b'<'):
                    h = re.sub(rb'\s', b'', it[1:-1])
                    txt += decode(bytes.fromhex(h.decode()), font)
            if txt:
                pieces.append({'x': round(lx, 1), 'y': round(ly, 1), 'f': fname, 't': txt})
        if t.isalpha() or t in (b"'", b'"', b'T*'):
            stack = [] if t not in (b'Tf',) else []
        else:
            stack.append(t)
    return pieces

def main(src, out):
    data = open(src, 'rb').read()
    objs = objects(data)
    # ページ順は /Pages の /Kids の順（オブジェクト番号順ではない）。増分更新で同じ番号が複数あるときは後のもの（最新）を使う
    root = [n for n, b in objs.items() if re.search(rb'/Type\s*/Pages', b) and b'/Kids' in b]
    kids = re.search(rb'/Kids\s*\[([^\]]*)\]', objs[root[0]]).group(1)
    pages = [int(k) for k in re.findall(rb'(\d+)\s+0\s+R', kids)]
    result = []
    for n in pages:
        b = objs[n]
        fonts = page_fonts(objs, b)
        cids = [int(x) for x in re.findall(rb'/Contents\s+(\d+)\s+0\s+R', b)]
        m = re.search(rb'/Contents\s*\[([^\]]*)\]', b)
        if m:
            cids = [int(x) for x in re.findall(rb'(\d+)\s+0\s+R', m.group(1))]
        content = b''.join(stream_of(objs[c]) or b'' for c in cids)
        result.append({'page_obj': n, 'pieces': run(content, fonts)})
    json.dump(result, open(out, 'w'), ensure_ascii=False, indent=0)
    print(len(result), 'pages', sum(len(p['pieces']) for p in result), 'pieces', 'unknown', sorted(UNKNOWN.items())[:40])

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
