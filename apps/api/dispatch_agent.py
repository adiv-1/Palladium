"""Dispatch agent: reads an incident and the units on campus, recommends who should go.

It only recommends. A person approves on the command view before any unit is contacted.
With no API key, or if the model call fails or refuses, a deterministic rules plan is used
and labelled as such.
"""
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .config import settings

IncidentType = Literal['person_down', 'altercation', 'rack_activity', 'other']
Urgency = Literal['critical', 'high', 'moderate', 'low']
Priority = Literal['P1', 'P2', 'P3']
FALLBACK_BETA = 'server-side-fallback-2026-07-01'


# ---- request / response -----------------------------------------------------------------

class BuildingInfo(BaseModel):
    name: str
    address: str | None = None
    floors: str | None = None


class IncidentIn(BaseModel):
    id: str
    type: IncidentType
    severity: str
    place: str
    observation: str
    evidence: list[str] = []
    uncertainties: list[str] = []
    position: list[float] = Field(min_length=2, max_length=2)
    building: BuildingInfo | None = None


class UnitIn(BaseModel):
    id: str
    callsign: str
    mode: Literal['foot', 'bike', 'cart']
    status: str
    position: list[float] = Field(min_length=2, max_length=2)
    eta_seconds: float
    distance_m: float


class PolicyHint(BaseModel):
    priority: Priority
    units: int = Field(ge=1, le=6)
    reason: str


class RecommendRequest(BaseModel):
    incident: IncidentIn
    units: list[UnitIn]
    policy_hint: PolicyHint


class UnitPlan(BaseModel):
    unit_id: str
    callsign: str
    role: str
    reason: str
    message: str


class Recommendation(BaseModel):
    recommendation_id: str
    source: Literal['agent', 'rules']
    model: str | None
    urgency: Urgency
    priority: Priority
    overview: str
    units: list[UnitPlan]
    support: list[str]
    cautions: list[str]
    latency_ms: int
    note: str | None = None


class ApproveRequest(BaseModel):
    recommendation_id: str
    approved_unit_ids: list[str]
    note: str = Field(default='', max_length=1000)


# ---- what the model returns --------------------------------------------------------------

class AgentUnit(BaseModel):
    unit_id: str
    role: str
    reason: str
    message: str


class AgentPlan(BaseModel):
    urgency: Urgency
    priority: Priority
    overview: str
    units: list[AgentUnit]
    support: list[str]
    cautions: list[str]


SYSTEM = """You are the dispatch assistant for a university campus safety team. A camera triage system flagged a
possible incident. You recommend which on-campus units should respond. A human dispatcher reviews your
recommendation and approves it before any unit is contacted; you never contact anyone yourself.

Choose only from the units listed as available. Weigh travel time first, then distance and mode (bike and
cart units cover ground faster; foot units are fine when very close). The policy hint is the team's default
for this incident type; follow it unless the unit positions give a clear reason to differ, and say why.

Calibrate urgency: a person down or a physical altercation is usually high, critical when the evidence is
strong or the severity is high priority; activity at a bike rack is usually low or moderate. Detections are
uncalibrated and can be wrong, so say what to verify on arrival.

Write the overview in at most two plain sentences for the dispatcher. Write each unit message (under 240
characters) to that officer: where to go, what was seen, what to do, and how to approach. Use only facts in
the input. Never state or imply identity, demographics, guilt, intent or bicycle ownership. Put requests for
outside help, such as EMS for a person down, in support, phrased as a request. Put uncertainty in cautions."""


def _policy_urgency(req: RecommendRequest) -> Urgency:
    t, sev = req.incident.type, req.incident.severity
    if t == 'person_down':
        return 'critical' if sev == 'HIGH_PRIORITY_REVIEW' else 'high'
    if t == 'altercation':
        return 'critical' if sev == 'HIGH_PRIORITY_REVIEW' else 'high'
    return 'low' if t == 'rack_activity' else 'moderate'


def _where(req: RecommendRequest) -> str:
    b = req.incident.building
    if b and b.address:
        return f'{b.name} ({b.address})'
    return b.name if b else req.incident.place


def rules_plan(req: RecommendRequest, note: str) -> Recommendation:
    started = time.perf_counter()
    available = [u for u in req.units if u.status == 'available']
    prefer = 'bike' if req.incident.type == 'rack_activity' else None
    ranked = sorted(available, key=lambda u: u.eta_seconds * (0.6 if u.mode == prefer else 1))
    chosen = ranked[:req.policy_hint.units]
    where, what = _where(req), req.incident.observation
    action = {
        'person_down': 'Check on the person and render aid; EMS has been requested.',
        'altercation': 'Approach together, separate the parties and assess injuries.',
        'rack_activity': 'Check the bike rack. Ownership cannot be judged from video; treat it as a check, not a stop.',
        'other': 'Check the location and report what you find.',
    }[req.incident.type]
    units = [UnitPlan(unit_id=u.id, callsign=u.callsign, role='primary' if n == 0 else 'backup',
                      reason=f'{"Fastest" if n == 0 else "Next fastest"} available ({u.mode}, about {round(u.eta_seconds)} s).',
                      message=f'{req.policy_hint.priority} to {where}: {what}. {action}'[:240])
             for n, u in enumerate(chosen)]
    support = ['Request EMS to the scene'] if req.incident.type == 'person_down' else []
    return Recommendation(
        recommendation_id=_new_id(), source='rules', model=None, urgency=_policy_urgency(req),
        priority=req.policy_hint.priority,
        overview=(f'{req.policy_hint.reason} Nearest available: {", ".join(u.callsign for u in chosen)}.'
                  if chosen else 'No units are available.'),
        units=units, support=support, cautions=list(req.incident.uncertainties)[:3],
        latency_ms=round((time.perf_counter() - started) * 1000), note=note)


def _new_id():
    return 'rec_' + secrets.token_urlsafe(9)


def _api_key():
    return settings.anthropic_api_key or os.getenv('ANTHROPIC_API_KEY', '')


def _log_usage(model, usage, started, **extra):
    row = {'ts': time.time(), 'model': model, 'latency_ms': round((time.perf_counter() - started) * 1000),
           'input_tokens': getattr(usage, 'input_tokens', None), 'output_tokens': getattr(usage, 'output_tokens', None),
           'cached_tokens': getattr(usage, 'cache_read_input_tokens', None), **extra}
    with open(settings.data_dir / 'dispatch_agent_usage.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


async def call_model(req: RecommendRequest):
    """One structured call to Claude. Returns (AgentPlan | None, model name, stop_reason)."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=_api_key())
    started = time.perf_counter()
    response = await client.beta.messages.parse(
        model=settings.dispatch_agent_model,
        max_tokens=4000,
        betas=[FALLBACK_BETA],
        fallbacks='default',
        output_config={'effort': 'low'},
        system=SYSTEM,
        messages=[{'role': 'user', 'content': json.dumps({
            'incident': req.incident.model_dump(),
            'units': [u.model_dump() for u in req.units],
            'policy_hint': req.policy_hint.model_dump(),
        })}],
        output_format=AgentPlan,
    )
    _log_usage(response.model, response.usage, started, incident_id=req.incident.id, stop_reason=response.stop_reason)
    if response.stop_reason == 'refusal':
        return None, response.model, 'refusal'
    return response.parsed_output, response.model, response.stop_reason


_store: dict[str, tuple[Recommendation, RecommendRequest]] = {}


def _remember(rec: Recommendation, req: RecommendRequest):
    _store[rec.recommendation_id] = (rec, req)
    while len(_store) > 200:
        _store.pop(next(iter(_store)))
    return rec


async def recommend(req: RecommendRequest) -> Recommendation:
    if not _api_key():
        return _remember(rules_plan(req, 'No ANTHROPIC_API_KEY set; rules-based plan.'), req)
    started = time.perf_counter()
    try:
        plan, model, stop = await call_model(req)
    except Exception as error:  # network, auth, rate limit: never block dispatch on the agent
        return _remember(rules_plan(req, f'Agent unavailable ({type(error).__name__}); rules-based plan.'), req)
    if plan is None:
        return _remember(rules_plan(req, f'Agent returned no plan ({stop}); rules-based plan.'), req)
    available = {u.id: u for u in req.units if u.status == 'available'}
    units, seen = [], set()
    for u in plan.units:
        if u.unit_id in available and u.unit_id not in seen:
            seen.add(u.unit_id)
            units.append(UnitPlan(unit_id=u.unit_id, callsign=available[u.unit_id].callsign, role=u.role[:40],
                                  reason=u.reason[:300], message=u.message[:240]))
    dropped = len(plan.units) - len(units)
    if not units:
        return _remember(rules_plan(req, 'Agent chose no available unit; rules-based plan.'), req)
    rec = Recommendation(recommendation_id=_new_id(), source='agent', model=model, urgency=plan.urgency,
        priority=plan.priority, overview=plan.overview[:400], units=units, support=plan.support[:4],
        cautions=plan.cautions[:4], latency_ms=round((time.perf_counter() - started) * 1000),
        note=f'Dropped {dropped} unit(s) that were not available.' if dropped else None)
    return _remember(rec, req)


class ApprovalError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def approve(body: ApproveRequest) -> dict:
    found = _store.get(body.recommendation_id)
    if not found:
        raise ApprovalError(404, 'Unknown recommendation')
    rec, req = found
    planned = {u.unit_id: u for u in rec.units}
    if not body.approved_unit_ids or any(i not in planned for i in body.approved_unit_ids):
        raise ApprovalError(400, 'Approve one or more units from this recommendation')
    approved_at = datetime.now(timezone.utc).isoformat()
    units = [{'unit_id': i, 'callsign': planned[i].callsign, 'message': planned[i].message} for i in body.approved_unit_ids]
    with open(settings.data_dir / 'dispatch_audit.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps({'recommendation_id': rec.recommendation_id, 'incident_id': req.incident.id,
                            'urgency': rec.urgency, 'source': rec.source, 'approved_units': body.approved_unit_ids,
                            'note': body.note, 'approved_at': approved_at}) + '\n')
    return {'ok': True, 'approved_at': approved_at, 'units': units}
