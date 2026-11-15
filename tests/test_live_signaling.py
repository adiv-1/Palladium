"""The signaling relay authorizes peers, scopes rooms and never relays media."""
import time
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from apps.api.config import settings
from apps.api.db import Source, init, save
from apps.api.main import app
from apps.api.live import rooms
from apps.api.runtime import pairings


def setup_camera():
    init(recover=False)
    source = save(Source(name='WebRTC camera', kind='phone'))
    token = 'pair-' + source.id
    pairings[token] = {'source_id': source.id, 'expires': time.time()+60}
    return source.id, token


def test_live_signaling_requires_viewer_auth_and_valid_pairing():
    source, token = setup_camera()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect('/ws/live') as ws:
                ws.send_json({'role': 'viewer', 'source_id': source})
                ws.receive_json()
        assert rejected.value.code == 4401
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect('/ws/live') as ws:
                ws.send_json({'role': 'capture', 'token': 'invalid'})
                ws.receive_json()
        assert rejected.value.code == 4401


def test_live_signaling_trickles_ice_in_either_join_order_and_cleans_up():
    source, token = setup_camera()
    with TestClient(app) as client:
        client.cookies.set('safesc_token', settings.app_token)
        with client.websocket_connect('/ws/live') as viewer:
            viewer.send_json({'role': 'viewer', 'source_id': source})
            v = viewer.receive_json()
            assert v['type'] == 'ready' and isinstance(v['ice_servers'], list)
            with client.websocket_connect('/ws/live') as phone:
                phone.send_json({'role': 'capture', 'token': token})
                p = phone.receive_json()
                assert phone.receive_json() == {'type': 'viewer_joined', 'peer_id': v['peer_id']}
                assert viewer.receive_json() == {'type': 'capture_ready', 'peer_id': p['peer_id']}
                description = {'type': 'offer', 'sdp': 'v=0\r\n'}
                phone.send_json({'type': 'offer', 'peer_id': v['peer_id'], 'description': description})
                assert viewer.receive_json() == {'type': 'offer', 'peer_id': p['peer_id'], 'description': description}
                candidate = {'candidate': 'candidate:test', 'sdpMid': '0'}
                viewer.send_json({'type': 'candidate', 'peer_id': p['peer_id'], 'candidate': candidate})
                assert phone.receive_json() == {'type': 'candidate', 'peer_id': v['peer_id'], 'candidate': candidate}
                with client.websocket_connect('/ws/live') as second:
                    second.send_json({'role': 'viewer', 'source_id': source})
                    s = second.receive_json()
                    assert second.receive_json()['peer_id'] == p['peer_id']
                    assert phone.receive_json() == {'type': 'viewer_joined', 'peer_id': s['peer_id']}
                assert phone.receive_json() == {'type': 'peer_left', 'peer_id': s['peer_id']}
                assert viewer.receive_json() == {'type': 'peer_left', 'peer_id': s['peer_id']}
            assert viewer.receive_json() == {'type': 'peer_left', 'peer_id': p['peer_id']}
    assert source not in rooms


def test_live_signaling_scopes_rooms_and_rejects_duplicate_camera():
    source, token = setup_camera()
    other_source, other_token = setup_camera()
    with TestClient(app) as client:
        with client.websocket_connect('/ws/live') as phone:
            phone.send_json({'role': 'capture', 'token': token})
            phone.receive_json()
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect('/ws/live') as duplicate:
                    duplicate.send_json({'role': 'capture', 'token': token})
                    duplicate.receive_json()
            assert rejected.value.code == 4409
            with client.websocket_connect('/ws/live') as other:
                other.send_json({'role': 'capture', 'token': other_token})
                other.receive_json()
                # Targeting a peer from a different room never forwards a message.
                other.send_json({'type': 'candidate', 'peer_id': next(iter(rooms[source])), 'candidate': None})
                phone.send_json({'type': 'pong'})
    assert source not in rooms and other_source not in rooms


@pytest.mark.parametrize('payload', [[], {'role': 'capture', 'token': []}, {'role': 'viewer', 'source_id': []}])
def test_live_signaling_rejects_malformed_hello(payload):
    with TestClient(app) as client:
        client.cookies.set('safesc_token', settings.app_token)
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect('/ws/live') as ws:
                ws.send_json(payload)
                ws.receive_json()
        assert rejected.value.code == 4400


@pytest.mark.parametrize('payload', [[], {'type': 'offer', 'peer_id': []}])
def test_live_signaling_rejects_malformed_relay(payload):
    source, token = setup_camera()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect('/ws/live') as ws:
                ws.send_json({'role': 'capture', 'token': token})
                ws.receive_json()
                ws.send_json(payload)
                ws.receive_json()
        assert rejected.value.code == 4400
    assert source not in rooms


def test_live_diagnostics_are_authenticated_minimized_and_transient():
    source, token = setup_camera()
    with TestClient(app) as client:
        assert client.get('/api/live/status', params={'source_id': source}).status_code == 401
        client.cookies.set('safesc_token', settings.app_token)
        with client.websocket_connect('/ws/live') as phone:
            phone.send_json({'role': 'capture', 'token': token})
            p = phone.receive_json()
            assert p['diagnostics_supported'] is True
            with client.websocket_connect('/ws/live') as viewer:
                viewer.send_json({'role': 'viewer', 'source_id': source})
                v = viewer.receive_json()
                viewer.receive_json()
                phone.receive_json()
                # Ordered subsequent relay round-trip ensures diagnosis was processed.
                phone.send_json({'type': 'diagnostics', 'remote_peer_id': v['peer_id'],
                    'connection_state': 'failed', 'ice_connection_state': 'failed',
                    'ice_gathering_state': 'complete', 'frames_encoded': 123, 'fps': 59.8,
                    'width': 1920, 'height': 1080, 'sdp': 'secret-sdp', 'address': '10.0.0.1',
                    'credential': 'secret-credential', 'bytes_received': -1})
                phone.send_json({'type': 'offer', 'peer_id': v['peer_id'],
                    'description': {'type': 'offer', 'sdp': 'not-retained'}})
                viewer.receive_json()
                status = client.get('/api/live/status', params={'source_id': source}).json()
                camera = next(p for p in status['peers'] if p['role'] == 'capture')
                assert camera['signals_forwarded']['offer'] == 1
                connection = camera['connections'][0]
                assert connection['connection_state'] == 'failed' and connection['frames_encoded'] == 123
                assert 'bytes_received' not in connection
                assert not any(value in str(status) for value in ['secret-sdp', '10.0.0.1', 'secret-credential', 'not-retained'])
            assert phone.receive_json()['type'] == 'peer_left'
        status = client.get('/api/live/status', params={'source_id': source}).json()
        assert not status['peers']
        assert any(p['role'] == 'capture' and p['connections'] for p in status['recent_peers'])
        assert client.get('/api/live/status', params={'source_id': 'missing'}).status_code == 404


def test_private_turn_credentials_are_short_lived_hmac_and_secret_stays_backend(monkeypatch):
    import base64
    import hashlib
    import hmac
    from pydantic import SecretStr
    from apps.api import live
    secret = '0123456789abcdef'*4
    monkeypatch.setattr(settings, 'turn_urls', 'turn:relay.example:3478?transport=udp,turn:relay.example:443?transport=tcp')
    monkeypatch.setattr(settings, 'turn_shared_secret', SecretStr(secret))
    monkeypatch.setattr(live.time, 'time', lambda: 1000000000)
    servers = live.ice_servers()
    relay = servers[-1]
    assert relay['username'].startswith('1000003600:')
    assert relay['credential'] == base64.b64encode(hmac.new(secret.encode(), relay['username'].encode(), hashlib.sha1).digest()).decode()
    assert relay['urls'] == ['turn:relay.example:3478?transport=udp', 'turn:relay.example:443?transport=tcp']
    assert secret not in str(servers)
    assert live.ice_servers()[-1]['username'] != relay['username']
    monkeypatch.setattr(settings, 'turn_urls', '["turns:relay.example:443?transport=tcp"]')
    assert live.ice_servers()[-1]['urls'] == ['turns:relay.example:443?transport=tcp']


@pytest.mark.parametrize('urls,secret', [('', 'x'*32), ('turn:relay.example', ''), ('https://relay.example', 'x'*32), ('turn:relay.example', 'short'), ('[]', 'x'*32)])
def test_private_turn_rejects_incomplete_or_invalid_configuration(monkeypatch, urls, secret):
    from pydantic import SecretStr
    from apps.api.live import ice_servers
    monkeypatch.setattr(settings, 'turn_urls', urls)
    monkeypatch.setattr(settings, 'turn_shared_secret', SecretStr(secret))
    with pytest.raises(ValueError):
        ice_servers()
