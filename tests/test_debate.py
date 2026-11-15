import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api import debate
from apps.api.config import settings
from apps.api.main import app

SCENARIOS = {
    'assault': ('altercation', dict(people_count=2, contact_seconds=2.5, mutual_motion=0.15, motion_alternation=0.1, peak_speed=3.4,
                                    fall_after_contact=1, flee_after=1, post_event_still_s=6, duration_s=5)),
    'playfight': ('altercation', dict(people_count=2, contact_seconds=4, mutual_motion=0.85, motion_alternation=0.8, peak_speed=2.2,
                                      fall_after_contact=0, flee_after=0, duration_s=9)),
    'snatch': ('rack_activity', dict(people_count=2, contact_seconds=0.8, mutual_motion=0.2, motion_alternation=0.1, peak_speed=3.6,
                                     object_transfer=1, flee_after=1, duration_s=4)),
    'ownbag': ('rack_activity', dict(people_count=1, contact_seconds=0, peak_speed=0.6, object_transfer=0, flee_after=0,
                                     bag_on_ground_before=1, duration_s=5)),
    'hug': ('person_down', dict(people_count=2, contact_seconds=3.5, mutual_motion=0.9, motion_alternation=0.15, peak_speed=0.8,
                                post_event_still_s=1.5, duration_s=6)),
    'down': ('person_down', dict(people_count=1, peak_speed=1.2, post_event_still_s=18, duration_s=20)),
}
FORBIDDEN = ('guilty', 'suspect', 'criminal', 'race', 'gender', 'ethnic', 'identity')


def evidence(name, **kw):
    kind, feats = SCENARIOS[name]
    return debate.DebateEvidence(incident_type=kind, observation=f'{name} scenario', features=feats, **kw)


def run(name, gate=None):
    return asyncio.run(debate.run_debate(evidence(name), gate or debate.RulesGate()))


def test_play_fight_is_downgraded_or_suppressed_and_advocate_wins():
    r = run('playfight')
    assert r.decision in ('suppress', 'downgrade') and r.advocate == 'won'
    assert r.benign['play_fighting'] > r.threat_score


def test_assault_escalates():
    r = run('assault')
    assert r.decision == 'escalate' and r.advocate == 'lost'
    assert r.threat['one_sided_aggression'] > 0.8


def test_snatching_escalates():
    r = run('snatch')
    assert r.decision == 'escalate' and r.threat['object_taken_forcibly'] > 0.8


def test_own_bag_pickup_is_suppressed():
    r = run('ownbag')
    assert r.decision == 'suppress' and r.advocate == 'won'


def test_person_down_escalates_and_hug_does_not():
    assert run('down').decision == 'escalate'
    assert run('hug').decision == 'suppress'


def test_empty_evidence_is_not_suppressed():
    r = asyncio.run(debate.run_debate(debate.DebateEvidence(incident_type='other', observation='x'), debate.RulesGate()))
    assert r.decision == 'downgrade' and r.note


def test_brief_always_has_three_clean_bullets():
    for name in SCENARIOS:
        r = run(name)
        assert len(r.brief.bullets) == 3 and all(b.strip() for b in r.brief.bullets)
        text = ' '.join(r.brief.bullets).lower()
        assert not any(w in text for w in FORBIDDEN)
    r = asyncio.run(debate.run_debate(debate.DebateEvidence(incident_type='other', observation='x')))
    assert len(r.brief.bullets) == 3


def test_questions_run_in_parallel():
    calls = []

    class Slow:
        name, model = 'rules', None

        async def answer(self, ev, qs):
            calls.append(('start', qs[0].side))
            await asyncio.sleep(0.05)
            calls.append(('end', qs[0].side))
            return {q.id: 0.5 for q in qs}

    asyncio.run(debate.run_debate(evidence('assault'), Slow()))
    assert [c[0] for c in calls] == ['start', 'start', 'end', 'end']


class FakeClient:
    """Stands in for anthropic.AsyncAnthropic; answers every asked id from a fixed table."""

    def __init__(self, table, drop=None):
        self.table, self.drop, self.kwargs = table, drop, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    async def parse(self, **kwargs):
        self.kwargs.append(kwargs)
        ids = [q['id'] for q in json.loads(kwargs['messages'][0]['content'][-1]['text'])['questions']]
        answers = [debate._Answer(id=i, probability=self.table[i]) for i in ids if i != self.drop]
        return SimpleNamespace(parsed_output=debate._Answers(answers=answers), model='claude-haiku-5-5-test', stop_reason='end_turn',
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))


def table(**over):
    base = {q.id: 0.1 for q in debate.QUESTIONS}
    return {**base, **over}


def test_claude_gate_with_fake_client():
    client = FakeClient(table(forceful_contact=0.9, one_sided_aggression=0.8, play_fighting=0.1))
    r = asyncio.run(debate.run_debate(evidence('assault', image_b64s=['aGk=']), debate.ClaudeGate(client)))
    assert r.source == 'claude' and r.model == 'claude-haiku-5-5-test' and r.decision == 'escalate' and r.note is None
    assert len(client.kwargs) == 2                                     # one call per question set
    k = client.kwargs[0]
    assert k['betas'] == [debate.FALLBACK_BETA] and k['fallbacks'] == 'default' and k['output_config'] == {'effort': 'low'}
    assert k['model'] == settings.debate_model and k['messages'][0]['content'][0]['type'] == 'image'
    usage = (settings.data_dir / 'debate_usage.jsonl').read_text().splitlines()
    assert json.loads(usage[-1])['output_tokens'] == 5


def test_claude_gate_missing_id_falls_back_to_rules():
    client = FakeClient(table(), drop='play_fighting')
    r = asyncio.run(debate.run_debate(evidence('assault'), debate.ClaudeGate(client)))
    assert r.source == 'rules' and r.note and 'claude gate unavailable' in r.note and r.decision == 'escalate'


def test_gate_failure_and_jev_fall_back_to_rules():
    class Broken:
        name, model = 'claude', 'x'

        async def answer(self, ev, qs):
            raise RuntimeError('boom')

    r = asyncio.run(debate.run_debate(evidence('playfight'), Broken()))
    assert r.source == 'rules' and r.model is None and 'unavailable' in r.note and len(r.brief.bullets) == 3
    j = asyncio.run(debate.run_debate(evidence('assault'), debate.JevGate()))
    assert j.source == 'rules' and 'vendor API spec' in j.note
    with pytest.raises(debate.NotConfigured):
        asyncio.run(debate.JevGate().answer(evidence('assault'), debate.THREAT_QS))


def test_get_gate_selection(monkeypatch):
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'debate_gate', 'auto')
    assert debate.get_gate().name == 'rules'
    monkeypatch.setattr(settings, 'anthropic_api_key', 'k')
    assert debate.get_gate().name == 'claude'
    monkeypatch.setattr(settings, 'debate_gate', 'rules')
    assert debate.get_gate().name == 'rules'
    monkeypatch.setattr(settings, 'debate_gate', 'jev')
    assert debate.get_gate().name == 'jev'


def body(name='playfight'):
    kind, feats = SCENARIOS[name]
    return {'incident_type': kind, 'observation': 'x', 'features': feats}


def test_endpoint_needs_login_and_returns_brief(monkeypatch):
    monkeypatch.setattr(settings, 'anthropic_api_key', '')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(settings, 'debate_gate', 'auto')
    with TestClient(app) as c:
        assert c.post('/api/debate', json=body()).status_code == 401
        assert c.post('/api/login', json={'token': 'test-workspace-token'}).status_code == 200
        r = c.post('/api/debate', json=body())
        assert r.status_code == 200
        data = r.json()
        assert data['source'] == 'rules' and len(data['brief']['bullets']) == 3 and data['advocate'] == 'won'
        assert c.post('/api/debate', json={**body(), 'image_b64s': ['a'] * 5}).status_code == 422
