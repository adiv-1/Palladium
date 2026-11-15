import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from apps.api.db import Source, init, save
from apps.api.main import app
from apps.api.phone_alerts import relay_phone_alerts
from apps.api.runtime import VideoSession, hub, pairings
from apps.api.video import encode


@pytest.mark.asyncio
async def test_phone_alerts_are_source_scoped_minimal_and_deduplicated():
    queue = asyncio.Queue()
    received = []

    class Socket:
        async def send_json(self, message):
            received.append(message)

    task = asyncio.create_task(relay_phone_alerts(Socket(), queue, 'own-camera', True))
    def event(source, status='saving_context'):
        return {'type': 'source', 'payload': {'id': source, 'telemetry': {
            'geolocation': {'latitude': 34}, 'review_alerts': [{'id': 'notice',
            'status': status, 'message': 'Possible contact', 'private': 'excluded'}]}}}
    try:
        for item in [event('other-camera'), {'type': 'incident', 'payload': {'id': 'own-camera'}},
                     event('own-camera'), event('own-camera'), event('own-camera', 'saved')]:
            await queue.put(item)
        await asyncio.sleep(.01)
        assert received == [{'type': 'phone_alert', 'alert': {'id': 'notice',
            'status': status, 'simulated': True, 'message': 'Possible contact'}}
            for status in ['saving_context', 'saved']]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_capture_receives_phone_alert_without_disturbing_frame_ack_or_stop(monkeypatch):
    init(recover=False)
    source = save(Source(name='Phone notification test', kind='phone'))
    token = 'phone-alert-' + source.id
    pairings[token] = {'source_id': source.id, 'expires': time.time()+60}
    original_push = VideoSession.push

    async def push_and_flag(self, frame):
        await original_push(self, frame)
        await hub.publish('source', {'id': self.source.id, 'telemetry': {'review_alert': {
            'id': 'test-flag', 'status': 'saving_context', 'message': 'Test flagged event'}}})

    monkeypatch.setattr(VideoSession, 'push', push_and_flag)
    jpeg = encode(np.zeros((48, 64, 3), dtype=np.uint8))
    with TestClient(app) as client:
        baseline = len(hub.listeners)
        with client.websocket_connect('/ws/capture') as ws:
            ws.send_json({'token': token})
            assert ws.receive_json()['type'] == 'ready'
            ws.send_json({'type': 'frame', 'timestamp': 10})
            ws.send_bytes(jpeg)
            messages = [ws.receive_json(), ws.receive_json()]
            assert {message['type'] for message in messages} == {'ack', 'phone_alert'}
            alert = next(message['alert'] for message in messages if message['type'] == 'phone_alert')
            assert alert['simulated'] is True
            ws.send_json({'type': 'stop'})
            assert ws.receive_json()['type'] == 'stopped'
        # Allow endpoint cleanup to complete before inspecting subscriptions.
        client.portal.call(asyncio.sleep, .02)
        assert len(hub.listeners) == baseline
    pairings.pop(token, None)


def test_expired_pairing_cannot_subscribe_to_phone_alerts():
    from starlette.websockets import WebSocketDisconnect
    token = 'expired-phone-notification-test'
    pairings[token] = {'source_id': 'unknown', 'expires': 0}
    with TestClient(app) as client:
        baseline = len(hub.listeners)
        with client.websocket_connect('/ws/capture') as ws:
            ws.send_json({'token': token})
            with pytest.raises(WebSocketDisconnect) as error:
                ws.receive_json()
            assert error.value.code == 4401
        assert len(hub.listeners) == baseline
    pairings.pop(token, None)
