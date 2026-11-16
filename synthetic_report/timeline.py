"""Saved synthetic prediction timeline. No calculation, writes, or network."""
import argparse, html
from collections import defaultdict
from datetime import datetime
import report

def build(predictions, race=None):
    groups = defaultdict(list)
    for p in predictions:
        if p.get('mode') != 'synthetic_only' or p.get('official_performance_eligible') is not False:
            raise ValueError('synthetic only')
        if race is not None and p['race_key'] != race:
            continue
        at = p['at']
        if not isinstance(at, datetime) or at.utcoffset() is None:
            raise ValueError('aware timestamp required')
        groups[(p['race_key'], p['method'], p['version'])].append(p)
    output = []
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda p:(p['at'], p['pred_id']))
        times = [p['at'] for p in rows]
        if len(times) != len(set(times)):
            raise ValueError('ambiguous same-time predictions')
        previous = None
        entries = []
        for p in rows:
            candidates = []
            for c in p['output']['trio_candidates'][:10]:
                ids = c['horses']
                if type(ids) is not list or len(ids)!=3 or any(type(i) is not str or not i for i in ids) or len(set(ids))!=3:
                    raise ValueError('invalid trio')
                candidates.append(tuple(sorted(ids)))
            if len(set(candidates))!=len(candidates):
                raise ValueError('duplicate trio')
            current = set(candidates)
            entries.append(dict(at=p['at'].isoformat(), candidates=candidates,
                                added=None if previous is None else sorted(current-previous),
                                removed=None if previous is None else sorted(previous-current)))
            previous = current
        output.append(dict(race=key[0],method=key[1],version=key[2],entries=entries))
    return output

def render(groups):
    esc=lambda x:html.escape(str(x),quote=True)
    trio=lambda xs: '、'.join(esc(' / '.join(x)) for x in xs) or 'なし'
    sections=[]
    for g in groups:
        entries=[]
        for e in g['entries']:
            delta='比較元なし' if e['added'] is None else '上位10組への追加：'+trio(e['added'])+'<br>上位10組からの除外：'+trio(e['removed'])
            entries.append('<article><h3>'+esc(e['at'])+'</h3><p>'+delta+'</p><details><summary>保存済み候補を見る</summary><ol>'+''.join('<li>'+trio([c])+'</li>' for c in e['candidates'])+'</ol></details></article>')
        sections.append('<section><h2>'+esc(g['race'])+'</h2><p>'+esc(g['method'])+' / '+esc(g['version'])+'</p>'+''.join(entries)+'</section>')
    return '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>予測の時点比較</title><style>body{font-family:sans-serif;max-width:48rem;margin:auto;padding:1rem;background:#f3f8fb;color:#173047}article{background:white;padding:1rem;border-left:4px solid #447b99;margin:1rem 0;overflow-wrap:anywhere}aside{background:#fff0bd;padding:1rem}summary{padding:.7rem;cursor:pointer}</style><h1>予測の時点比較</h1><aside>架空専用。実予想・正式成績・購入提案ではありません。未学習・未較正です。</aside><p>同じレース・方式・版の保存済み時点を比較します。上位10組の集合差であり、買い目変更の指示ではありません。変化理由は保存されていないため表示しません。順位だけの変更は集合差に含みません。予測の再計算・注文・実投票なし。</p>'''+(''.join(sections) or '<p>該当する保存済み予測はありません。</p>')+'</html>'

def main():
    parser=argparse.ArgumentParser(description='架空予測の時点比較')
    parser.add_argument('journal'); parser.add_argument('--race', help='保存済みrace_keyとの完全一致')
    args=parser.parse_args()
    try:
        predictions,_=report.load(args.journal)
        print(render(build(predictions,args.race)))
    except (ValueError,KeyError,TypeError,AttributeError,report.sqlite3.Error,OSError):
        parser.exit(2,'保留：保存済み予測を比較できません。元DBは変更していません。\n')
if __name__=='__main__': main()
