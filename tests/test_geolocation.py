import copy
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from apps.api.config import settings
from apps.api.db import Source, Incident, init, rows, save
from apps.api.geolocation import Geolocation
from apps.api.main import app
from apps.api.runtime import VideoSession, pairings, sessions
from apps.api.video import Frame, encode

NOW = 1_800_000_000_000


def fix(**changes):
    return {'type': 'geolocation', 'status': 'active', 'latitude': 34.02,
        'longitude': -118.28, 'accuracy_m': 8.5, 'measured_at': NOW, **changes}


def test_opt_in_permission_denial_revoke_and_saved_snapshot_immutability():
    geo = Geolocation()
    assert geo.snapshot(10) == {'status': 'disabled', 'provenance': 'browser_permission'}
    assert geo.update({'status': 'denied'}, 0, now_ms=NOW, monotonic=0)['accepted']
    assert 'latitude' not in geo.current
    assert geo.update(fix(), 5, now_ms=NOW, monotonic=1)['accepted']
    saved = geo.snapshot(6)
    assert saved['latitude'] == 34.02 and saved['accuracy_m'] == 8.5
    assert saved['provenance'] == 'browser_permission'
    assert geo.update({'status': 'disabled', 'latitude': 42}, 8, now_ms=NOW, monotonic=2)['accepted']
    assert 'latitude' not in geo.current
    assert geo.snapshot(6)['latitude'] == 34.02  # earlier event survives a later opt-out
    assert geo.snapshot(9)['status'] == 'disabled'
    assert saved['latitude'] == 34.02  # earlier independent saved records retain provenance
    assert geo.update({'status': 'unavailable'}, 9)['accepted']
    assert geo.snapshot(10)['status'] == 'unavailable'


@pytest.mark.parametrize('changes', [
    {'latitude': True}, {'latitude': 10**1000}, {'longitude': float('nan')}, {'accuracy_m': float('inf')},
    {'measured_at': False}, {'latitude': -91}, {'latitude': 91},
    {'longitude': -181}, {'longitude': 181}, {'accuracy_m': -1},
    {'accuracy_m': 100001}, {'measured_at': NOW-300001},
    {'measured_at': NOW+60001}, {'latitude': '34.02'}, {'status': []},
])
def test_invalid_geolocation_never_changes_accepted_state(changes):
    geo = Geolocation()
    before = copy.deepcopy(geo.current)
    answer = geo.update(fix(**changes), 10, now_ms=NOW, monotonic=0)
    assert answer['accepted'] is False
    assert geo.current == before and not geo.history


def test_nearest_preceding_measurement_and_staleness_and_rate_limit():
    geo = Geolocation()
    assert geo.update(fix(), 10, now_ms=NOW, monotonic=0)['accepted']
    assert geo.update(fix(latitude=35), 11, now_ms=NOW+1000, monotonic=1)['reason'] == 'rate_limited'
    assert geo.update(fix(latitude=36, measured_at=NOW+10000), 20, now_ms=NOW+10000, monotonic=10)['accepted']
    assert geo.snapshot(15)['latitude'] == 34.02  # do not attach the newer location to an older event
    assert geo.snapshot(21)['latitude'] == 36
    assert geo.snapshot(9)['status'] == 'unavailable'
    stale = geo.snapshot(81)
    assert stale['status'] == 'stale' and 'latitude' not in stale
    # Changes in permission status bypass the five-second fix throttle.
    assert geo.update({'status': 'denied'}, 21, now_ms=NOW+11000, monotonic=11)['accepted']
    assert geo.update(fix(measured_at=NOW+12000), 22, now_ms=NOW+12000, monotonic=12)['accepted']
    for i in range(100):
        geo.update(fix(measured_at=NOW+20000+i*5000), 30+i*5, now_ms=NOW+20000+i*5000, monotonic=20+i*5)
    assert len(geo.history) == 90


@pytest.mark.asyncio
async def test_location_survives_frame_telemetry_and_incident_is_frozen(monkeypatch):
    monkeypatch.setattr(settings, 'post_event_seconds', 0)
    monkeypatch.setattr(settings, 'enable_relay_vsr', False)
    monkeypatch.setattr(settings, 'enable_relift', False)
    monkeypatch.setattr(settings, 'enable_clip_review', False)
    init(recover=False)
    source = save(Source(name='GPS camera', kind='phone'))
    session = VideoSession(source)
    jpeg = encode(np.zeros((48, 64, 3), dtype=np.uint8))
    await session.push(Frame(0, 0, jpeg))
    await session.update_geolocation(fix(measured_at=time.time()*1000))
    await session.push(Frame(.5, 1, jpeg))
    assert session.source.telemetry['geolocation']['latitude'] == 34.02
    assert session.source.telemetry['geolocation']['accuracy_m'] == 8.5
    session.bookmark()
    await session.push(Frame(1, 2, jpeg))
    if session.persistence_tasks: await __import__('asyncio').gather(*list(session.persistence_tasks))
    incident = next(i for i in rows(Incident) if i.source_id == source.id)
    saved = copy.deepcopy(incident.record['geolocation'])
    assert saved['status'] == 'active' and saved['latitude'] == 34.02
    await session.update_geolocation({'status': 'disabled'})
    await session.push(Frame(1.5, 3, jpeg))
    assert 'latitude' not in session.source.telemetry['geolocation']
    assert incident.record['geolocation'] == saved
    await session.close()
    assert 'latitude' not in session.source.telemetry['geolocation']


def test_capture_json_geolocation_does_not_consume_frame_bytes_or_disconnect():
    init(recover=False)
    source = save(Source(name='GPS websocket', kind='phone'))
    token = 'geolocation-'+source.id
    pairings[token] = {'source_id': source.id, 'expires': time.time()+60}
    jpeg = encode(np.zeros((48, 64, 3), dtype=np.uint8))
    with TestClient(app) as client:
        with client.websocket_connect('/ws/capture') as ws:
            ws.send_json({'token': token})
            ready=ws.receive_json()
            assert ready['type'] == 'ready' and ready['geolocation'] is True
            ws.send_json({'type': 'geolocation', 'status': 'denied'})
            assert ws.receive_json()['status'] == 'denied'
            ws.send_json(fix(latitude='invalid', measured_at=time.time()*1000))
            rejected = ws.receive_json()
            assert rejected['type'] == 'geolocation' and not rejected['accepted']
            ws.send_json({'type': 'frame', 'timestamp': 10})
            ws.send_bytes(jpeg)
            assert ws.receive_json()['type'] == 'ack'
            ws.send_json(fix(measured_at=time.time()*1000))
            assert ws.receive_json()['status'] == 'active'
            ws.send_json({'type': 'geolocation', 'status': 'disabled'})
            assert ws.receive_json()['status'] == 'disabled'
            ws.send_json({'type': 'frame', 'timestamp': 10.5})
            ws.send_bytes(jpeg)
            assert ws.receive_json()['index'] == 2
            ws.send_json({'type': 'stop'})
            assert ws.receive_json()['type'] == 'stopped'
    assert source.id not in sessions


@pytest.mark.asyncio
async def test_uploaded_video_never_backfills_a_phone_location(monkeypatch):
    monkeypatch.setattr(settings, 'post_event_seconds', 0)
    monkeypatch.setattr(settings, 'enable_relay_vsr', False)
    monkeypatch.setattr(settings, 'enable_relift', False)
    monkeypatch.setattr(settings, 'enable_clip_review', False)
    init(recover=False)
    session = VideoSession(save(Source(name='Uploaded footage', kind='upload')))
    jpeg = encode(np.zeros((48, 64, 3), dtype=np.uint8))
    await session.push(Frame(0, 0, jpeg))
    await session.push(Frame(.5, 1, jpeg))
    # Even an accidentally attached session fix must not become uploaded-video GPS.
    await session.update_geolocation(fix(measured_at=time.time()*1000))
    session.bookmark()
    await session.push(Frame(1, 2, jpeg))
    if session.persistence_tasks:
        await __import__('asyncio').gather(*list(session.persistence_tasks))
    item = next(i for i in rows(Incident) if i.source_id == session.source.id)
    assert item.record['geolocation']['status'] == 'unavailable'
    assert 'latitude' not in item.record['geolocation']
    await session.close()


def test_cached_fix_after_event_cannot_backfill_and_revoke_marker_wins_ties():
    geo = Geolocation()
    geo.update(fix(measured_at=NOW-30000), 20, now_ms=NOW, monotonic=0)
    assert geo.snapshot(19)['status'] == 'unavailable'
    assert geo.snapshot(21)['latitude'] == 34.02
    geo.update({'status':'disabled'}, 25, now_ms=NOW+5000, monotonic=5)
    assert geo.snapshot(24)['latitude'] == 34.02
    assert geo.snapshot(25)['status'] == 'disabled'
    geo.update(fix(measured_at=NOW+5000), 25, now_ms=NOW+5000, monotonic=6)
    assert geo.snapshot(25)['status'] == 'active'
    geo.update({'status':'denied'}, 25, now_ms=NOW+5000, monotonic=7)
    assert geo.snapshot(25)['status'] == 'denied'
    geo.clear()
    assert not geo.history and 'latitude' not in geo.current


@pytest.mark.asyncio
async def test_camera_stop_preserves_prior_event_geotag_then_clears_session_history(monkeypatch):
    monkeypatch.setattr(settings, 'post_event_seconds', 10)
    monkeypatch.setattr(settings, 'enable_relay_vsr', False)
    monkeypatch.setattr(settings, 'enable_relift', False)
    monkeypatch.setattr(settings, 'enable_clip_review', False)
    init(recover=False)
    session = VideoSession(save(Source(name='Stop GPS camera', kind='phone')))
    jpeg = encode(np.zeros((48, 64, 3), dtype=np.uint8))
    await session.push(Frame(0, 0, jpeg))
    await session.update_geolocation(fix(measured_at=time.time()*1000))
    await session.push(Frame(.5, 1, jpeg))
    session.bookmark()
    # No new camera frame arrives between marking the moment and stopping.
    # The phone stops its watcher before stopping video; pending earlier events
    # still use the location permission/fix that was active at their source time.
    await session.update_geolocation({'status': 'disabled'})
    assert 'latitude' not in session.source.telemetry['geolocation']
    await session.close()
    item = next(i for i in rows(Incident) if i.source_id == session.source.id)
    assert item.record['geolocation']['status'] == 'active'
    assert item.record['geolocation']['latitude'] == 34.02
    assert not session.geolocation.history
    assert session.source.telemetry['geolocation']['status'] == 'disabled'
