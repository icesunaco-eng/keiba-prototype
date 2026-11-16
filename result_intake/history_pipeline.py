"""Offline multi-profile integration. Explicit local manifest, never network access."""
import json,argparse
from pathlib import Path
from profile_history import batch
from history_asof import select

def run(manifest,root):
    if manifest.get('schema')!='saved-profile-manifest-v1':raise ValueError('manifest_schema')
    root=Path(root).resolve();saved=[]
    def local(name):
        p=(root/name).resolve()
        if not p.is_relative_to(root):raise ValueError('snapshot_path_outside_root')
        return p
    if not isinstance(manifest.get('profiles'),list) or not manifest['profiles']:raise ValueError('profiles_required')
    for item in manifest['profiles']:
        raw=local(item['html']).read_bytes();metadata=json.loads(local(item['metadata']).read_text())
        saved.append((raw,metadata))
    reports=batch(saved)
    gate=select(reports,as_of=manifest['as_of'],target_start=manifest['target_start'],target_race_ref=manifest['target_race_ref'])
    gate['profile_count']=len(reports)
    gate['history_row_count']=sum(len(r['rows']) for r in reports)
    gate['target_is_hypothetical']=True  # no confirmed-entry validation in this tool
    return gate

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('manifest');p.add_argument('--root',required=True)
    args=p.parse_args();print(json.dumps(run(json.loads(Path(args.manifest).read_text()),args.root),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
