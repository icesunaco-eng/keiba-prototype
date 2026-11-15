"""Read-only, synthetic journal report. No predictor, network, or orders."""
import argparse
import hashlib
import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path

def decode(x):
    if set(x) == {'__datetime__'}:
        return datetime.fromisoformat(x['__datetime__'])
    return x

def encode(x):
    if isinstance(x, datetime):
        return {'__datetime__': x.isoformat()}
    raise TypeError('unsupported type')

def digest(x):
    return hashlib.sha256(json.dumps(x, default=encode, sort_keys=True, allow_nan=False).encode()).hexdigest()

def load(path):
    # mode=ro never creates or modifies the source database.
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        events = [json.loads(p, object_hook=decode) for (p,) in db.execute('SELECT payload FROM events ORDER BY seq')]
    predictions = {}
    comparisons = {}
    for e in events:
        if e.get('schema') != 2 or e.get('evidence_hash') != digest({k:v for k,v in e.items() if k != 'evidence_hash'}):
            raise ValueError('journal evidence invalid')
        if e['request'].get('synthetic') is not True:
            raise ValueError('synthetic only')
        for p in e['saved_evidence']['predictions']:
            if p.get('mode') != 'synthetic_only' or p.get('official_performance_eligible') is not False:
                raise ValueError('synthetic only')
            key = p['pred_id']
            if key in predictions and predictions[key] != p:
                raise ValueError('conflicting prediction')
            predictions[key] = p
        for c in e['saved_evidence']['comparisons']:
            if c.get('synthetic') is not True or c.get('official_performance_eligible') is not False:
                raise ValueError('synthetic only')
            key = c['pred_id']
            if key in comparisons and comparisons[key] != c:
                raise ValueError('conflicting comparison')
            comparisons[key] = c
    if set(comparisons) - set(predictions):
        raise ValueError('orphan comparison')
    return list(predictions.values()), comparisons

def render(predictions, comparisons):
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    for p in predictions:
        c = comparisons.get(p['pred_id'])
        status = '結果未比較' if c is None else '架空結果との候補順位：' + esc(c['candidate_rank'])
        candidates = p['output']['trio_candidates'][:10]
        rows = ''.join('<li>' + esc(' / '.join(x['horses'])) + '</li>' for x in candidates)
        cards.append('<section><h2>' + esc(p['race_key']) + '</h2><p>' + esc(p['method']) + ' / ' + esc(p['version']) + '</p><p>予測時点：' + esc(p['at']) + '</p><p>' + status + '</p><h3>試算候補（上位10組まで）</h3><ol>' + rows + '</ol></section>')
    return '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>架空予測の確認</title><style>body{font-family:sans-serif;margin:1rem auto;padding:0 1rem;max-width:48rem;background:#f4faff;color:#172f43}section{background:white;padding:1rem;margin:1rem 0;border:1px solid #b5d9ee;border-radius:12px;overflow-wrap:anywhere}aside{background:#fff2cb;padding:1rem}</style><h1>架空予測の確認</h1><aside>架空データ専用の開発確認です。計算の重み・確率は未学習・未較正。実予測・正式成績・購入提案ではありません。金額配分・注文・実投票はありません。</aside><p>保存済み出力を表示しています。予測の再計算はしていません。</p>''' + (''.join(cards) or '<p>保存済み予測はありません。</p>') + '</html>'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('journal')
    args = parser.parse_args()
    try:
        ps, cs = load(args.journal)
        print(render(ps, cs))
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError):
        parser.exit(2, '保留：保存済み架空証拠を検証できません。元DBは変更していません。\n')

if __name__ == '__main__':
    main()
