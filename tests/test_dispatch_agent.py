import json

import pytest
from fastapi.testclient import TestClient

from apps.api import dispatch_agent
from apps.api.config import settings
from apps.api.main import app


def body(**incident_overrides):
    incident = {'id': 'inc-1', 'type': 'altercation', 'severity': 'HIGH_PRIORITY_REVIEW', 'place': 'Lyon Recreation Center',
                'observation': 'person_1 and person_6 in close contact with rapid movement', 'evidence': ['6 fast samples'],
                'uncertainties': ['Sport can trigger this.'], 'position': [-118.2884, 34.0244],
                'building': {'name': 'Lyon Recreation Center', 'address': '1026 W 34th St', 'floors': '3'}, **incident_overrides}
    units = [
        {'id': 'u-far', 'callsign': 'Trojan 40', 'mode': 'bike', 'status': 'available', 'position': [0, 0], 'eta_seconds': 300, 'distance_m': 900},
        {'id': 'u-near', 'callsign': 'Trojan 31', 'mode': 'cart', 'status': 'available', 'position': [0, 0], 'eta_seconds': 40, 'distance_m': 200},
        {'id': 'u-mid', 'callsign': 'Trojan 14', 'mode': 'foot', 'status': 'available', 'position': [0, 0], 'eta_seconds': 120, 'distance_m': 160},
        {'id': 'u-busy', 'callsign': 'Trojan 22', 'mode': 'bike', 'status': 'en_route', 'position': [0, 0], 'eta_seconds': 10, 'distance_m': 30},
    ]
    return {'incident': incident, 'units': units, 'policy_hint': {'priority': 'P1', 'units': 2, 'reason': 'Send two units.'}}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    with TestClient(app) as c:
        assert c.post('/api/login', json={'token': 'test-workspace-token'}).status_code == 200
        yield c


def test_rules_fallback_without_key_picks_nearest_available(client):
    r = client.post('/api/dispatch/recommend', json=body()).json()
    assert r['source'] == 'rules' and 'ANTHROPIC_API_KEY' in r['note']
    assert [u['unit_id'] for u in r['units']] == ['u-near', 'u-mid']   # busy unit skipped
    assert r['urgency'] == 'critical' and r['priority'] == 'P1'
    assert all(len(u['message']) <= 240 for u in r['units'])
    rack = client.post('/api/dispatch/recommend', json={**body(type='rack_activity', severity='WATCH'),
                                                        'policy_hint': {'priority': 'P3', 'units': 1, 'reason': 'Check.'}}).json()
    assert rack['urgency'] == 'low' and len(rack['units']) == 1


def test_agent_plan_drops_unknown_and_unavailable_units(client, monkeypatch):
    monkeypatch.setattr(settings, 'anthropic_api_key', 'test-key')

    async def fake(req):
        return dispatch_agent.AgentPlan(urgency='high', priority='P1', overview='Two units to Lyon.', support=[], cautions=['Could be sport.'],
            units=[dispatch_agent.AgentUnit(unit_id='u-busy', role='primary', reason='closest', message='go'),
                   dispatch_agent.AgentUnit(unit_id='ghost', role='backup', reason='?', message='go'),
                   dispatch_agent.AgentUnit(unit_id='u-near', role='primary', reason='fast cart', message='Go to Lyon, separate parties.')]), 'claude-opus-5-5', 'end_turn'
    monkeypatch.setattr(dispatch_agent, 'call_model', fake)
    r = client.post('/api/dispatch/recommend', json=body()).json()
    assert r['source'] == 'agent' and r['model'] == 'claude-opus-5-5'
    assert [u['unit_id'] for u in r['units']] == ['u-near'] and r['units'][0]['callsign'] == 'Trojan 31'
    assert 'Dropped 2' in r['note']

    async def none_valid(req):
        return dispatch_agent.AgentPlan(urgency='high', priority='P1', overview='x', support=[], cautions=[],
            units=[dispatch_agent.AgentUnit(unit_id='u-busy', role='p', reason='r', message='m')]), 'm', 'end_turn'
    monkeypatch.setattr(dispatch_agent, 'call_model', none_valid)
    assert client.post('/api/dispatch/recommend', json=body()).json()['source'] == 'rules'

    async def broken(req):
        raise RuntimeError('network down')
    monkeypatch.setattr(dispatch_agent, 'call_model', broken)
    r = client.post('/api/dispatch/recommend', json=body()).json()
    assert r['source'] == 'rules' and 'RuntimeError' in r['note']


def test_approve_writes_audit_and_rejects_bad_ids(client):
    rec = client.post('/api/dispatch/recommend', json=body()).json()
    rid = rec['recommendation_id']
    assert client.post('/api/dispatch/approve', json={'recommendation_id': 'nope', 'approved_unit_ids': ['u-near']}).status_code == 404
    assert client.post('/api/dispatch/approve', json={'recommendation_id': rid, 'approved_unit_ids': []}).status_code == 400
    assert client.post('/api/dispatch/approve', json={'recommendation_id': rid, 'approved_unit_ids': ['u-busy']}).status_code == 400
    ok = client.post('/api/dispatch/approve', json={'recommendation_id': rid, 'approved_unit_ids': ['u-near'], 'note': 'go'}).json()
    assert ok['ok'] and ok['units'][0]['callsign'] == 'Trojan 31' and ok['units'][0]['message']
    lines = (settings.data_dir / 'dispatch_audit.jsonl').read_text().splitlines()
    last = json.loads(lines[-1])
    assert last['recommendation_id'] == rid and last['approved_units'] == ['u-near'] and last['incident_id'] == 'inc-1'


def test_dispatch_endpoints_need_login():
    with TestClient(app) as c:
        assert c.post('/api/dispatch/recommend', json=body()).status_code == 401
        assert c.post('/api/dispatch/approve', json={'recommendation_id': 'x', 'approved_unit_ids': ['y']}).status_code == 401
