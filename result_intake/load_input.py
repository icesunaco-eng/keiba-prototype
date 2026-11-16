"""Read existing JSON bytes directly. Print a non-sensitive staging summary only."""
import argparse,hashlib,json
from pathlib import Path
from intake import stage

def unique(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise ValueError('duplicate JSON key')
        result[k]=v
    return result

def read_json(path):
    raw=Path(path).read_bytes()
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('non-finite JSON')))
    if type(value) is not dict:raise ValueError('JSON object required')
    return value,hashlib.sha256(raw).hexdigest()

def summarize(record_path,meta_path):
    record,raw_hash=read_json(record_path)
    meta,meta_hash=read_json(meta_path)
    result=stage(record,meta)
    return {'state':result['state'],'entries':len(result['entries']),
            'history_rows':len(result['history_rows']),'model_ready':result['model_ready'],
            'prediction_created':result['prediction_created'],'order_created':result['order_created'],
            'normalized_raw_sha256':raw_hash,'metadata_raw_sha256':meta_hash,
            'normalized_canonical_sha256':result['normalized_input_hash'],
            'blockers':result['blockers']}

def main():
    parser=argparse.ArgumentParser(description='既存結果JSONの読取確認。モデル実行なし。')
    parser.add_argument('normalized_json');parser.add_argument('metadata_json')
    args=parser.parse_args()
    try: print(json.dumps(summarize(args.normalized_json,args.metadata_json),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError,AttributeError,OSError):
        parser.exit(2,'保留：既存入力を検証できません。元資料は変更していません。\n')
if __name__=='__main__':main()
