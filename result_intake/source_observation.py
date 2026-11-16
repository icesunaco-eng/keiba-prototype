"""Recover acquisition evidence from saved logs, not mtime or HTTP modification."""
import copy,hashlib
from reviewed_podium import SOURCE_SHA256
FETCH_LOG_SHA='c0d217da01480fd9b170b388e69ece910dd69c1057372a8ee9f6eabaef453c04'
HEADERS_SHA='133797e91c7610fe3f6be699d1598732c31a7759a1fb8c8ff72462db71d7b3ca'
ACQUIRED_AT='2026-11-15T07:10:37Z'

def attach(dataset,pdf_bytes,fetch_log,headers):
    """Exact archived evidence only. Headers may contain cookies: never emit them."""
    for data,sha in [(pdf_bytes,SOURCE_SHA256),(fetch_log,FETCH_LOG_SHA),(headers,HEADERS_SHA)]:
        if not isinstance(data,bytes) or hashlib.sha256(data).hexdigest()!=sha:raise ValueError('unrecognized_source_evidence')
    if fetch_log.decode().strip()!=ACQUIRED_AT:raise ValueError('observation_time_conflict')
    if len(pdf_bytes)!=718025:raise ValueError('source_size')
    if dataset.get('source_kind')!='post_race_results' or not dataset.get('records') or any(r.get('source_sha256')!=SOURCE_SHA256 for r in dataset['records']):raise ValueError('dataset_source')
    result=copy.deepcopy(dataset)
    result['provenance'].update(acquired_at=ACQUIRED_AT,observed_at=ACQUIRED_AT,published_at=None,
        time_status='saved_acquisition_log_confirmed',acquisition_evidence=dict(fetch_log_sha256=FETCH_LOG_SHA,headers_sha256=HEADERS_SHA),
        pre_race_as_of=None,prediction_input_allowed=False)
    result['unavailable']=[x for x in result['unavailable'] if x!='source_acquired_at']
    result.update(model_ready=False,production_ready=False)
    return result
