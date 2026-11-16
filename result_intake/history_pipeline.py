"""Offline multi-profile integration. Explicit local manifest, never network access."""
import json,argparse,sys
from pathlib import Path
from profile_history import batch
from history_asof import select,instant

MAX_PROFILES=64

def run(manifest,root):
    required={'schema','profiles','as_of','target_start','target_race_ref'}
    if not isinstance(manifest,dict) or set(manifest)!=required:raise ValueError('manifest_fields')
    if manifest['schema']!='saved-profile-manifest-v1':raise ValueError('manifest_schema')
    if not isinstance(manifest['target_race_ref'],str) or not manifest['target_race_ref'].strip():raise ValueError('target_reference_required')
    try:
        cutoff=instant(manifest['as_of']);target=instant(manifest['target_start'])
    except (ValueError,TypeError):raise ValueError('manifest_times') from None
    if cutoff>=target:raise ValueError('prestart_cutoff_required')
    profiles=manifest['profiles']
    if not isinstance(profiles,list) or not profiles:raise ValueError('profiles_required')
    if len(profiles)>MAX_PROFILES:raise ValueError('profile_limit_64')
    if not isinstance(root,(str,Path)) or not str(root):raise ValueError('snapshot_root_required')
    try:root=Path(root).resolve()
    except (OSError,RuntimeError):raise ValueError('snapshot_root_unavailable') from None
    saved=[];paths=[];seen=set()
    # Validate every item and normalized path before opening any snapshot.
    for item in profiles:
        if not isinstance(item,dict) or set(item)!={'html','metadata'}:raise ValueError('profile_item_fields')
        pair=[]
        for field in ('html','metadata'):
            name=item[field]
            if not isinstance(name,str) or not name.strip():raise ValueError('snapshot_path_required')
            try:p=(root/name).resolve()
            except (OSError,RuntimeError,ValueError):raise ValueError('snapshot_path_invalid') from None
            if not p.is_relative_to(root):raise ValueError('snapshot_path_outside_root')
            if p in seen:raise ValueError('duplicate_snapshot_path')
            seen.add(p);pair.append(p)
        paths.append(pair)
    for html_path,metadata_path in paths:
        try:
            raw=html_path.read_bytes();metadata=json.loads(metadata_path.read_text())
        except (OSError,UnicodeError,ValueError):raise ValueError('snapshot_read_or_json_failed') from None
        if not isinstance(metadata,dict) or not all(isinstance(metadata.get(k),str) and metadata[k].strip() for k in ('sha256','acquired_at','url')):raise ValueError('metadata_fields')
        saved.append((raw,metadata))
    reports=batch(saved)
    gate=select(reports,as_of=manifest['as_of'],target_start=manifest['target_start'],target_race_ref=manifest['target_race_ref'])
    gate['profile_count']=len(reports)
    gate['history_row_count']=sum(len(r['rows']) for r in reports)
    gate['target_is_hypothetical']=True  # no confirmed-entry validation in this tool
    return gate

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('manifest');p.add_argument('--root',required=True)
    args=p.parse_args()
    try:
        report=run(json.loads(Path(args.manifest).read_text()),args.root)
    except (ValueError,OSError,UnicodeError) as error:
        print('history_pipeline: '+str(error),file=sys.stderr)
        return 2
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
