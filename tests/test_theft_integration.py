"""SafeSC <-> theft-detection worker integration (apps/api/theft.py).

The worker is replaced by a small HTTP server that speaks the worker's API, so these tests run
without the worker's model or dependencies. tests/fixtures/theft_events_v1.json holds events
produced by the worker's real code.
"""
import asyncio
import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api import theft
from apps.api.config import settings
from apps.api.db import Incident, ReviewAction, get, init, rows
from apps.api.main import app

EVENTS = json.loads((Path(__file__).parent / 'fixtures' / 'theft_events_v1.json').read_text(encoding='utf-8'))['events']
SNATCH = next(e for e in EVENTS if e['event_type'] == 'possible_snatching')


def event(**overrides):
    e = copy.deepcopy(SNATCH)
    for key, value in overrides.items():
        if '.' in key:
            outer, inner = key.split('.')
            e[outer][inner] = value
        else:
            e[key] = value
    return e


def fresh_id(n):
    return f'evt_{n:016x}'


class FakeWorker:
    """Speaks the worker's HTTP API. mode: 'ok' | 'down' (500s) | 'slow' (sleeps)."""

    def __init__(self):
        self.events, self.evidence, self.reviews, self.mode = [], {}, [], 'ok'
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body=b'', ctype='application/json'):
                self.send_response(code); self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                if worker.mode == 'slow':
                    time.sleep(5)
                if worker.mode == 'down':
                    return self._send(500, b'{}')
                if self.path.startswith('/api/v1/status'):
                    return self._send(200, json.dumps({'status': 'running', 'detail': '', 'detector': 'yolox_tiny@sha256:test',
                                                       'simulated': False, 'source_id': 'laptop_webcam',
                                                       'metrics': {'analysed_fps': 10.0, 'capture_to_result_ms': {'p95': 36.0}}}).encode())
                if self.path.startswith('/api/v1/events'):
                    return self._send(200, json.dumps(list(reversed(worker.events))).encode())
                if self.path.startswith('/api/v1/frame.jpg'):
                    return self._send(200, b'\xff\xd8fakejpeg', 'image/jpeg')
                if self.path.startswith('/api/v1/evidence/'):
                    name = self.path.rsplit('/', 1)[1]
                    return self._send(200, worker.evidence[name], 'application/octet-stream') if name in worker.evidence else self._send(404)
                self._send(404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                worker.reviews.append((self.path, body))
                self._send(200, b'{}')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def worker(monkeypatch):
    w = FakeWorker()
    monkeypatch.setattr(settings, 'theft_worker_url', w.url)
    monkeypatch.setattr(settings, 'theft_poll_seconds', 0.2)
    monkeypatch.setattr(settings, 'theft_timeout_seconds', 1.0)
    monkeypatch.setattr(theft, 'state', theft._State())
    yield w
    w.close()


def login(c):
    assert c.post('/api/login', json={'token': 'test-workspace-token'}).status_code == 200


def wait_until(predicate, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# ---- feature switch -------------------------------------------------------------------
def test_disabled_by_default_restores_baseline(monkeypatch):
    monkeypatch.setattr(settings, 'theft_worker_url', '')
    with TestClient(app) as c:
        login(c)
        assert c.get('/api/health').json()['theft_worker'] == {'enabled': False}
        assert c.post('/api/theft/events', json=SNATCH).status_code == 404
        assert c.get('/api/theft/frame').status_code == 404


def test_theft_routes_require_safesc_sign_in(worker):
    with TestClient(app) as c:
        assert c.get('/api/theft/status').status_code == 401
        assert c.post('/api/theft/events', json=SNATCH).status_code == 401


# ---- event compatibility --------------------------------------------------------------
@pytest.mark.asyncio
async def test_every_worker_fixture_event_is_accepted_and_mapped():
    init()
    theft.state = theft._State()
    for n, payload in enumerate(EVENTS):
        payload = {**payload, 'event_id': fresh_id(0xA000 + n)}
        outcome, incident = await theft.ingest(payload)
        assert outcome == 'created', outcome
        stored = get(Incident, payload['event_id'])
        assert stored.severity == 'REVIEW_REQUIRED' and stored.review_status == 'pending'
        assert stored.record['category'] == payload['event_type']
        assert stored.record['trigger_origin'] == 'theft_worker'
        assert stored.created_at == payload['timestamp_utc'].replace('Z', '+00:00')          # timestamp preserved
        assert stored.start_time == payload['evidence']['onset_stream_s']
        assert stored.confidence == payload['evidence_score'] and stored.simulated is True
        assert stored.record['observable_events'][0]['observation'] == payload['observation']
        assert stored.record['model']['revision'] == payload['model_version']
        assert 'uncalibrated' in stored.record['confidence_note']


@pytest.mark.asyncio
async def test_duplicate_event_is_suppressed():
    init()
    theft.state = theft._State()
    payload = event(event_id=fresh_id(0xB001))
    assert (await theft.ingest(payload))[0] == 'created'
    assert (await theft.ingest(payload))[0] == 'duplicate'
    assert len([i for i in rows(Incident) if i.id == payload['event_id']]) == 1
    assert theft.state.duplicates == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [
    {'event_id': 'evt_123'},
    {'event_type': 'theft'},
    {'schema_version': '2.0'},
    {'evidence_score': 1.5},
    {'timestamp_utc': '2026-11-14T15:00:00'},
    {'observation': 'The suspect stole a bag.'},
    {'evidence.onset_stream_s': -1},
])
async def test_malformed_or_unsupported_events_are_rejected(bad):
    init()
    theft.state = theft._State()
    outcome, incident = await theft.ingest(event(**{'event_id': fresh_id(0xC000), **bad}))
    assert outcome.startswith('rejected') and incident is None
    assert theft.state.rejected == 1 and theft.state.last_rejection


@pytest.mark.asyncio
async def test_missing_field_is_rejected_and_info_events_never_become_incidents():
    init()
    theft.state = theft._State()
    broken = event(event_id=fresh_id(0xC100)); del broken['evidence']
    assert (await theft.ingest(broken))[0].startswith('rejected')
    info = event(event_id=fresh_id(0xC101), severity='info', event_type='uncertain_interaction')
    assert (await theft.ingest(info)) == ('info', None)
    assert get(Incident, info['event_id']) is None
    assert theft.state.info_events[-1]['event_id'] == info['event_id']


@pytest.mark.asyncio
async def test_worker_review_status_maps_to_safesc_decisions():
    init()
    theft.state = theft._State()
    for n, (worker_status, safesc_status) in enumerate(theft.TO_SAFESC.items()):
        payload = event(event_id=fresh_id(0xD000 + n), review_status=worker_status)
        _, incident = await theft.ingest(payload)
        assert incident.review_status == safesc_status


# ---- end to end through the poller ----------------------------------------------------
def test_poller_creates_incident_with_evidence_and_review_flows_back(worker):
    payload = event(event_id=fresh_id(0xE001))
    worker.events.append(payload)
    with TestClient(app) as c:
        login(c)
        assert wait_until(lambda: get(Incident, payload['event_id']) is not None)
        health = c.get('/api/health').json()['theft_worker']
        assert health['enabled'] and health['reachable'] and health['pipeline'] == 'running'
        assert health['analysed_fps'] == 10.0 and health['incidents_created'] == 1

        # evidence becomes available later on the worker; SafeSC attaches it to the same incident
        worker.evidence = {f"{payload['event_id']}.webm": b'WEBMDATA', f"{payload['event_id']}.jpg": b'\xff\xd8JPEG'}
        worker.events[0] = {**payload, 'evidence': {**payload['evidence'],
                                                    'clip_reference': f"{payload['event_id']}.webm",
                                                    'frame_reference': f"{payload['event_id']}.jpg"}}
        assert wait_until(lambda: get(Incident, payload['event_id']).record['files'].get('theft_evidence'))
        listed = [i for i in c.get('/api/incidents').json() if i['id'] == payload['event_id']]
        assert len(listed) == 1                                   # still exactly one incident
        assert c.get(f"/api/incidents/{payload['event_id']}/media/theft_evidence").content == b'WEBMDATA'
        assert c.get(f"/api/incidents/{payload['event_id']}/media/thumbnail").content == b'\xff\xd8JPEG'

        r = c.post(f"/api/incidents/{payload['event_id']}/review", json={'decision': 'needs_review', 'note': 'staged'})
        assert r.status_code == 200 and r.json()['review_status'] == 'needs_review'
        assert wait_until(lambda: worker.reviews)
        path, body = worker.reviews[-1]
        assert path == f"/api/v1/events/{payload['event_id']}/review" and body == {'status': 'uncertain', 'note': 'staged'}
        assert any(a.incident_id == payload['event_id'] for a in rows(ReviewAction))
        assert c.get('/api/theft/frame').content == b'\xff\xd8fakejpeg'


def test_push_endpoint_accepts_and_rejects(worker):
    with TestClient(app) as c:
        login(c)
        r = c.post('/api/theft/events', json=event(event_id=fresh_id(0xE101)))
        assert r.status_code == 200 and r.json()['outcome'] in ('created', 'duplicate')
        assert c.post('/api/theft/events', json=event(event_id='bad')).status_code == 422


# ---- failure isolation ----------------------------------------------------------------
def test_worker_crash_is_reported_and_safesc_keeps_working(worker):
    worker.mode = 'down'
    with TestClient(app) as c:
        login(c)
        assert wait_until(lambda: theft.state.last_error is not None)
        h = c.get('/api/health').json()['theft_worker']
        assert h['reachable'] is False and 'HTTPStatusError' in h['last_error']
        assert c.get('/api/incidents').status_code == 200
        assert c.get('/api/theft/frame').status_code in (404, 503)
        worker.mode = 'ok'                                        # recovery
        assert wait_until(lambda: c.get('/api/health').json()['theft_worker']['reachable'])


def test_absent_worker_does_not_block_the_api(monkeypatch):
    monkeypatch.setattr(settings, 'theft_worker_url', 'http://127.0.0.1:9')     # nothing listens here
    monkeypatch.setattr(settings, 'theft_poll_seconds', 0.2)
    monkeypatch.setattr(theft, 'state', theft._State())
    with TestClient(app) as c:
        login(c)
        assert wait_until(lambda: theft.state.last_error is not None)
        t = time.perf_counter()
        assert c.get('/api/incidents').status_code == 200
        assert time.perf_counter() - t < 1.0
        assert c.get('/api/health').json()['theft_worker']['reachable'] is False


def test_slow_worker_does_not_block_the_api(worker):
    worker.mode = 'slow'                                          # every worker call takes 5 s
    with TestClient(app) as c:
        login(c)
        time.sleep(0.5)                                           # poller is now stuck waiting on the worker
        t = time.perf_counter()
        assert c.get('/api/incidents').status_code == 200
        assert c.get('/api/sources').status_code == 200
        assert time.perf_counter() - t < 1.0
        assert wait_until(lambda: theft.state.last_error is not None and 'Timeout' in theft.state.last_error, 5)
        worker.mode = 'ok'
