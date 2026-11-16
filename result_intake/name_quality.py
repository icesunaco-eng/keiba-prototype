"""Cross-engine name agreement is not independent factual confirmation."""
import re
def compare_names(rows,source_sha,expected_sha):
    if not isinstance(source_sha,str) or not re.fullmatch('[0-9a-f]{64}',source_sha) or source_sha!=expected_sha:
        raise ValueError('source_hash_mismatch')
    if not isinstance(rows,list) or not rows:raise ValueError('rows_required')
    results=[];keys=[(r.get('page'),r.get('race_no'),r.get('umaban')) for r in rows]
    for r,key in zip(rows,keys):
        a=r.get('name');b=r.get('pdfkit_name');reason=None
        if keys.count(key)!=1:reason='duplicate_identity'
        elif any(type(x) is not int for x in key) or not 1<=key[1]<=12 or not 1<=key[2]<=18:reason='invalid_identity'
        elif not isinstance(a,str) or not isinstance(b,str) or not a or not b:reason='missing_name'
        elif re.search(r'\ufffd|\{[^}]+\}',a+b):reason='unresolved_glyph'
        elif re.sub(r'\s','',a)!=re.sub(r'\s','',b):reason='engine_disagreement'
        results.append({'page':key[0],'race_no':key[1],'umaban':key[2],
                        'status':'hold' if reason else 'two_engine_agreement',
                        'reason':reason})
    return {'source_sha256':source_sha,'checks':results,'agreed':sum(x['reason'] is None for x in results),
            'holds':sum(x['reason'] is not None for x in results),'method':'custom-CID-vs-PDFKit-same-source',
            'not_verified':['stable_horse_id','finish','time','odds','payout','as_of_time'],
            'model_ready':False,'production_ready':False}
def prediction_gate(source_kind,name_report):
    # Name agreement alone can never authorize prediction use.
    return {'allowed':False,'reasons':['post_race_source' if source_kind=='post_race_results'
                                     else 'pre_race_provenance_and_full_quality_not_verified',
                                     'name_check_only_not_full_record_validation']}
