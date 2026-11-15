"""Isolated proposed model boundary. Not wired to System or production.
Reject missing evidence; no network, wagers, bankroll recommendations or model supplied.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

@dataclass(frozen=True)
class Observation:
    kind: str
    race_key: str
    source: str
    observed_at: datetime | None
    available_at: datetime | None
    usage_verified: bool
    synthetic: bool
    payload: tuple

@dataclass(frozen=True)
class PredictionRequest:
    race_key: str
    as_of: datetime
    scheduled_start: datetime
    mode: str
    observations: tuple[Observation, ...]

@dataclass(frozen=True)
class ModelOutput:
    model_id: str
    version: str
    synthetic: bool
    # Scores are opaque model output, not stake sizing or orders.
    scores: tuple

class Model(Protocol):
    def predict(self, request: PredictionRequest) -> ModelOutput: ...

def _contains_result_key(value):
    # A conservative diagnostic denylist, NOT a substitute for a verified feature schema.
    if isinstance(value,dict):
        return any((isinstance(k,str) and k.strip().lower() in {'payout','result','winner','settlement','finish_order'}) or _contains_result_key(v) for k,v in value.items())
    if isinstance(value,(tuple,list)):
        return any(_contains_result_key(x) for x in value)
    return False

REQUIRED = frozenset({'schedule','entries','odds','features'})

def gate(request: PredictionRequest) -> tuple[str,...]:
    reasons=[]
    if request.mode not in {'synthetic_test','real_shadow'}:reasons.append('unsupported_mode')
    if not request.race_key:reasons.append('missing_race_identity')
    times=[request.as_of,request.scheduled_start]
    if any(not isinstance(x, datetime) or x.tzinfo is None or x.utcoffset() is None for x in times):return ('timezone_unverified',)
    if request.as_of >= request.scheduled_start:reasons.append('not_pre_start')
    kinds={x.kind for x in request.observations}
    if not REQUIRED <= kinds:reasons.append('missing_required_observation')
    for o in request.observations:
        if o.kind not in REQUIRED:reasons.append('unapproved_input_kind')
        if o.race_key != request.race_key:reasons.append('race_identity_mismatch')
        if not isinstance(o.source,str) or not o.source.strip():reasons.append('missing_source')
        if o.usage_verified is not True:reasons.append('usage_unverified')
        if not o.payload:reasons.append('empty_payload')
        if request.mode=='real_shadow':reasons.append('payload_schema_unimplemented')
        if _contains_result_key(o.payload):reasons.append('result_payload_forbidden')
        if type(o.synthetic) is not bool or o.synthetic != (request.mode=='synthetic_test'):reasons.append('synthetic_real_mixture')
        if o.observed_at is None or o.available_at is None:reasons.append('missing_time_evidence');continue
        if any(not isinstance(t,datetime) or t.tzinfo is None or t.utcoffset() is None for t in [o.observed_at,o.available_at]):reasons.append('timezone_unverified');continue
        if o.observed_at > request.as_of or o.available_at > request.as_of:reasons.append('future_information')
    return tuple(sorted(set(reasons)))

def evaluate(request: PredictionRequest, model: Model | None) -> dict:
    errors=gate(request)
    if errors:return {'status':'held','reasons':errors,'model_called':False}
    if model is None:return {'status':'held','reasons':('model_not_implemented',),'model_called':False}
    result=model.predict(request)
    if (not result.model_id or not result.version or not result.scores or
        (type(result.synthetic) is not bool or result.synthetic != (request.mode=='synthetic_test')) or
        (request.mode=='real_shadow' and result.model_id=='dummy-fixed')):
        return {'status':'held','reasons':('model_output_unverified',),'model_called':True}
    return {'status':'boundary_only_not_adopted','model_called':True,'output':result,
            'eligible_for_official_performance':False,'order_created':False}
