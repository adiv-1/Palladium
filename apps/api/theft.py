"""Optional bridge to the theft-detection worker (services/theft_detection).

Disabled unless THEFT_WORKER_URL is set. The worker runs as its own process; SafeSC polls its
HTTP API, turns each review-level event into an ordinary SafeSC Incident, and forwards reviewer
decisions back. Nothing here imports the worker's code: the contract is the worker's event
schema 1.x, validated by the local model below.

Failure isolation: every worker call has a short timeout and every error is caught and
reported through ``status()``. A slow, crashed or absent worker never blocks SafeSC.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import settings
from .db import Incident, ReviewAction, get, save

EVENT_TYPES = ('possible_snatching', 'suspicious_object_removal', 'rapid_object_transfer',
               'possible_restricted_area_entry', 'uncertain_interaction')
# Worker review status <-> SafeSC review decision.
TO_SAFESC = {'pending': 'pending', 'confirmed': 'confirmed', 'dismissed': 'dismissed', 'uncertain': 'needs_review'}
TO_WORKER = {'confirmed': 'confirmed', 'dismissed': 'dismissed', 'needs_review': 'uncertain'}
_EVIDENCE_NAME = re.compile(r'^evt_[0-9a-f]{16}\.(jpg|webm)$')


class _Model(BaseModel):
    model_config = ConfigDict(extra='ignore')   # schema 1.x may add fields


class TrackRef(_Model):
    ref: str = Field(pattern=r'^(person|object)_[0-9]+$')
    role: str = Field(max_length=40)


class Evidence(_Model):
    clip_reference: str | None = None
    frame_reference: str | None = None
    onset_stream_s: float = Field(ge=0)
    detected_stream_s: float = Field(ge=0)
    detection_delay_s: float = Field(ge=0)
    processing_lag_s: float | None = None
    features: dict[str, float] = Field(default_factory=dict)
    tracks: list[TrackRef] = Field(default_factory=list, max_length=20)
    object_label: str | None = None
    object_ref: str | None = None
    zone_id: str | None = None


class TheftEvent(_Model):
    """Local copy of the worker's public event contract (schema 1.x)."""
    schema_version: str = Field(pattern=r'^1\.\d+$')
    event_id: str = Field(pattern=r'^evt_[0-9a-f]{16}$')
    source_id: str = Field(min_length=1, max_length=80)
    event_type: Literal[EVENT_TYPES]  # type: ignore[valid-type]
    timestamp_utc: datetime
    evidence_score: float = Field(ge=0, le=1)
    score_calibrated: bool = False
    severity: Literal['info', 'review']
    observation: str = Field(min_length=3, max_length=1000)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    evidence: Evidence
    model_version: str = Field(min_length=1, max_length=200)
    review_status: Literal['pending', 'confirmed', 'dismissed', 'uncertain'] = 'pending'
    simulated: bool = False

    @field_validator('timestamp_utc')
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError('timestamp_utc must be timezone-aware')
        return v.astimezone(timezone.utc)

    @field_validator('observation')
    @classmethod
    def _neutral(cls, v: str) -> str:
        # Same vocabulary rule SafeSC applies to its own observations (schemas.Observation).
        if re.search(r'\b(criminal|guilty|robbery|burglary|theft|assault|murder|rape|kidnapping|ethnicity|race|'
                     r'suspect|male|female|man|woman|men|women|boy|girl|disabled)\b', v, re.I):
            raise ValueError('Observation contains unsupported legal or personal inference')
        return v


class _State:
    def __init__(self) -> None:
        self.reachable = False
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self.worker: dict = {}
        self.received = 0
        self.created = 0
        self.duplicates = 0
        self.rejected = 0
        self.info_events: list[dict] = []
        self.last_rejection: str | None = None


state = _State()


def enabled() -> bool:
    return bool(settings.theft_worker_url)


def _headers() -> dict:
    return {'Authorization': f'Bearer {settings.theft_worker_token}'} if settings.theft_worker_token else {}


def status() -> dict:
    """Worker health for /api/health and the dashboard. Never raises."""
    if not enabled():
        return {'enabled': False}
    w = state.worker or {}
    metrics = w.get('metrics') or {}
    stale = state.last_ok is None or time.time() - state.last_ok > max(5.0, 4 * settings.theft_poll_seconds)
    return {'enabled': True, 'reachable': state.reachable and not stale, 'last_error': state.last_error,
            'pipeline': w.get('status', 'unknown'), 'detail': w.get('detail', ''),
            'detector': w.get('detector'), 'simulated': w.get('simulated'),
            'source_id': w.get('source_id'), 'analysed_fps': metrics.get('analysed_fps'),
            'capture_to_result_p95_ms': (metrics.get('capture_to_result_ms') or {}).get('p95'),
            'events_received': state.received, 'incidents_created': state.created,
            'duplicates_ignored': state.duplicates, 'rejected': state.rejected,
            'last_rejection': state.last_rejection, 'recent_info_events': state.info_events[-5:]}


def _record(ev: TheftEvent) -> dict:
    """Shape the event like the records SafeSC's queue and review page already display."""
    people = [t.ref for t in ev.evidence.tracks if t.ref.startswith('person_')]
    return {
        'category': ev.event_type,
        'trigger_origin': 'theft_worker',
        'observable_events': [{'timestamp_start': ev.evidence.onset_stream_s,
                               'timestamp_end': ev.evidence.detected_stream_s,
                               'actors': people, 'observation': ev.observation}],
        'uncertainties': ev.uncertainties or ['Derived from tracked boxes only; ownership and intent are not observable.'],
        'supporting_frames': [], 'frame_timestamps': [], 'clip_offset': 0,
        'model': {'name': 'theft_detection', 'revision': ev.model_version},
        'lighting_condition': 'not_assessed',
        'files': {}, 'original_sha256': None,
        'confidence_note': 'Heuristic evidence-quality score from the theft-detection worker; '
                           'uncalibrated, not a probability that anything wrongful happened.',
        'simulated': ev.simulated, 'trigger_time': ev.evidence.detected_stream_s,
        'transforms': [{'operation': 'received from theft-detection worker', 'event_id': ev.event_id,
                        'worker_source_id': ev.source_id, 'schema_version': ev.schema_version,
                        'evidence_media': 'annotated worker clip (boxes and temporary track numbers)'}],
        'model_output_audit': [], 'theft_event': ev.model_dump(mode='json'),
    }


async def ingest(payload: dict, client=None) -> tuple[str, Incident | None]:
    """Validate one event and store it. Returns (outcome, incident).

    outcome: 'created' | 'duplicate' | 'updated_evidence' | 'info' | 'rejected: <reason>'
    """
    from .runtime import hub
    state.received += 1
    try:
        ev = TheftEvent.model_validate(payload)
    except ValidationError as error:
        state.rejected += 1
        first = error.errors()[0]
        state.last_rejection = f"{'.'.join(map(str, first['loc']))}: {first['msg']}"[:300]
        return f'rejected: {state.last_rejection}', None
    if ev.severity != 'review':
        # Context only: shown in worker telemetry, never turned into an incident.
        if ev.event_id not in {e['event_id'] for e in state.info_events}:
            state.info_events = (state.info_events + [{'event_id': ev.event_id, 'event_type': ev.event_type,
                                                       'observation': ev.observation}])[-20:]
        return 'info', None
    existing = await asyncio.to_thread(get, Incident, ev.event_id)
    if existing is not None:
        if not existing.record.get('files') and (ev.evidence.clip_reference or ev.evidence.frame_reference) and client:
            if await _attach_evidence(existing, ev, client):
                await hub.publish('incident', existing.model_dump())
                return 'updated_evidence', existing
        state.duplicates += 1
        return 'duplicate', existing
    incident = Incident(id=ev.event_id, source_id=f'theft:{ev.source_id}',
                        created_at=ev.timestamp_utc.isoformat(),
                        start_time=ev.evidence.onset_stream_s, end_time=ev.evidence.detected_stream_s,
                        severity='REVIEW_REQUIRED', confidence=ev.evidence_score,
                        review_status=TO_SAFESC[ev.review_status], simulated=ev.simulated, record=_record(ev))
    await asyncio.to_thread(save, incident)
    state.created += 1
    if client and (ev.evidence.clip_reference or ev.evidence.frame_reference):
        await _attach_evidence(incident, ev, client)
    await hub.publish('incident', incident.model_dump())
    return 'created', incident


async def _attach_evidence(incident: Incident, ev: TheftEvent, client) -> bool:
    """Copy the worker's short clip/still into the incident folder. Best effort."""
    folder = settings.data_dir / 'incidents' / incident.id
    files, hashes = {}, {}
    for kind, name, target in (('theft_evidence', ev.evidence.clip_reference, 'theft_evidence.webm'),
                               ('thumbnail', ev.evidence.frame_reference, 'thumbnail.jpg')):
        if not name or not _EVIDENCE_NAME.match(name):
            continue
        try:
            r = await client.get(f'{settings.theft_worker_url}/api/v1/evidence/{name}', headers=_headers())
            if r.status_code != 200 or not r.content:
                continue
        except Exception as error:   # noqa: BLE001 - evidence is optional
            state.last_error = f'evidence fetch failed: {type(error).__name__}'
            continue
        folder.mkdir(parents=True, exist_ok=True)
        (folder / target).write_bytes(r.content)
        files[kind] = target
        hashes[kind] = sha256(r.content).hexdigest()
    if not files:
        return False
    fresh = await asyncio.to_thread(get, Incident, incident.id) or incident
    fresh.record = {**fresh.record, 'files': {**fresh.record.get('files', {}), **files},
                    'original_sha256': hashes.get('theft_evidence') or hashes.get('thumbnail'),
                    'evidence_sha256': hashes}
    await asyncio.to_thread(save, fresh)
    incident.record = fresh.record
    return True


async def forward_review(incident: Incident, decision: str, note: str) -> None:
    """Mirror a SafeSC review decision to the worker. Best effort; never raises."""
    if not enabled() or incident.record.get('trigger_origin') != 'theft_worker' or decision not in TO_WORKER:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.theft_timeout_seconds) as client:
            await client.post(f'{settings.theft_worker_url}/api/v1/events/{incident.id}/review',
                              json={'status': TO_WORKER[decision], 'note': note[:2000]}, headers=_headers())
    except Exception as error:   # noqa: BLE001
        state.last_error = f'review forward failed: {type(error).__name__}'


async def poller() -> None:
    """Background task: poll worker status and recent events. Runs only when enabled."""
    import httpx
    async with httpx.AsyncClient(timeout=settings.theft_timeout_seconds) as client:
        while True:
            try:
                r = await client.get(f'{settings.theft_worker_url}/api/v1/status', headers=_headers())
                r.raise_for_status()
                state.worker = r.json()
                r = await client.get(f'{settings.theft_worker_url}/api/v1/events', params={'limit': 50},
                                     headers=_headers())
                r.raise_for_status()
                for payload in reversed(r.json()):          # oldest first
                    await ingest(payload, client)
                state.reachable, state.last_ok, state.last_error = True, time.time(), None
            except asyncio.CancelledError:
                raise
            except Exception as error:   # noqa: BLE001 - the worker being down is a normal state
                state.reachable = False
                state.last_error = f'{type(error).__name__}: {str(error)[:200]}'
            await asyncio.sleep(settings.theft_poll_seconds)


router = APIRouter(prefix='/api/theft')


@router.get('/status')
def worker_status() -> dict:
    return status()


@router.post('/events')
async def push_event(payload: dict = Body(...)) -> dict:
    """Push alternative to polling (worker webhook with bearer_token = SafeSC app token)."""
    if not enabled():
        raise HTTPException(404, 'Theft detection is not enabled')
    outcome, incident = await ingest(payload)
    if outcome.startswith('rejected'):
        raise HTTPException(422, outcome)
    return {'outcome': outcome, 'incident_id': incident.id if incident else None}


@router.get('/frame')
async def worker_frame() -> Response:
    """Latest annotated frame from the worker, for SafeSC's live monitor."""
    if not enabled():
        raise HTTPException(404, 'Theft detection is not enabled')
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.theft_timeout_seconds) as client:
            r = await client.get(f'{settings.theft_worker_url}/api/v1/frame.jpg', headers=_headers())
    except Exception as error:   # noqa: BLE001
        raise HTTPException(503, f'Theft worker unavailable: {type(error).__name__}') from None
    if r.status_code != 200:
        raise HTTPException(404, 'No frame from the theft worker yet')
    return Response(r.content, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
