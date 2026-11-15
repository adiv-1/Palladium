"""Adversarial agent debate: a Threat Detection Agent vs a Devil's Advocate, before a human sees an alert.

No free-form debate text. Both sides answer fixed, typed questions as probabilities (0..1). The two
question sets are answered in parallel by a Gate; the result is a decision, a verdict on the advocate, and
a deterministic 3-bullet "Agent Consensus Brief" built from the top answers and the feature values.

Gates (all behind the same async protocol `answer(evidence, questions) -> {qid: p}`):
  RulesGate   deterministic, transparent formulas over kinematic features, instant, works offline.
  ClaudeGate  one structured Claude call per question set, calibrated probabilities only.
  JevGate     placeholder, see its docstring.

The debate only recommends. A human dispatcher decides. Nothing here infers identity, demographics,
intent or guilt; it scores how well observed motion matches a threat pattern or a benign pattern.
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from .config import settings

FALLBACK_BETA = 'server-side-fallback-2026-07-01'
ESCALATE_THREAT, ESCALATE_MARGIN, DOWNGRADE_THREAT = 0.55, 0.20, 0.40


# ---- evidence and questions ---------------------------------------------------------------

class DebateEvidence(BaseModel):
    incident_type: Literal['person_down', 'altercation', 'rack_activity', 'other']
    observation: str = Field(max_length=1000)
    rule_evidence: list[str] = Field(default=[], max_length=20)
    # Optional kinematic cues: people_count, contact_seconds, mutual_motion (0..1, both move symmetrically),
    # motion_alternation (0..1, back-and-forth rhythm), peak_speed (body heights/s), fall_after_contact (0/1),
    # flee_after (0/1), object_transfer (0/1), post_event_still_s, duration_s, bag_on_ground_before (0/1).
    features: dict[str, float] = {}
    image_b64s: list[str] = Field(default=[], max_length=4)

    @field_validator('image_b64s')
    @classmethod
    def _small_images(cls, v):
        if any(len(s) > 3_000_000 for s in v):
            raise ValueError('image too large')
        return v


@dataclass(frozen=True)
class Question:
    id: str
    side: Literal['threat', 'benign']
    text: str


QUESTIONS = [
    Question('forceful_contact', 'threat', 'Is there forceful physical contact between people?'),
    Question('victim_distress', 'threat', 'Does a person appear hurt, down, or unable to get up?'),
    Question('object_taken_forcibly', 'threat', 'Is an object pulled away from a person against their grip, followed by leaving?'),
    Question('one_sided_aggression', 'threat', 'Is the motion one-sided, with one person moving at and another reacting?'),
    Question('play_fighting', 'benign', 'Does this look like playful or mock fighting with matched, back-and-forth motion?'),
    Question('greeting_or_hug', 'benign', 'Does this look like a greeting or hug: calm, mutual, brief contact?'),
    Question('own_belongings', 'benign', 'Does this look like a person retrieving belongings they had just put down?'),
    Question('sport_or_dance', 'benign', 'Does this look like sport or dance: rhythmic, mutual, sustained motion?'),
]
THREAT_QS = [q for q in QUESTIONS if q.side == 'threat']
BENIGN_QS = [q for q in QUESTIONS if q.side == 'benign']
QTEXT = {q.id: q.text for q in QUESTIONS}
LABEL = {'forceful_contact': 'forceful contact', 'victim_distress': 'person hurt or down', 'object_taken_forcibly': 'object taken forcibly',
         'one_sided_aggression': 'one-sided aggression', 'play_fighting': 'play-fighting', 'greeting_or_hug': 'greeting or hug',
         'own_belongings': 'picking up own belongings', 'sport_or_dance': 'sport or dance'}
FEATURE_KEYS = ['people_count', 'contact_seconds', 'mutual_motion', 'motion_alternation', 'peak_speed', 'fall_after_contact',
                'flee_after', 'object_transfer', 'post_event_still_s', 'duration_s', 'bag_on_ground_before']


# ---- gates --------------------------------------------------------------------------------

class NotConfigured(RuntimeError):
    pass


class Gate(Protocol):
    name: str
    model: str | None

    async def answer(self, evidence: DebateEvidence, questions: list[Question]) -> dict[str, float]: ...


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def rules_probabilities(features: dict[str, float]) -> dict[str, float]:
    """Transparent formulas. Keep in sync with localDebate() in apps/dashboard/src/debate.ts.

    Notation: asym = 1 - mutual_motion; contact_f = min(contact_seconds / 1, 1); a missing cue takes the
    neutral default noted below. All results are clamped to 0..1.
    Defaults: people 2, contact 0, mutual 0.5, alternation 0.3, peak 0, fall/flee/transfer/bag 0, still 0, duration 0.
    """
    def g(key, default=0.0):
        v = features.get(key)
        return default if v is None else float(v)

    people, contact = g('people_count', 2), g('contact_seconds')
    mutual, alt = _clamp(g('mutual_motion', 0.5)), _clamp(g('motion_alternation', 0.3))
    peak, still, dur = g('peak_speed'), g('post_event_still_s'), g('duration_s')
    fall, flee = _clamp(g('fall_after_contact')), _clamp(g('flee_after'))
    transfer, bag = _clamp(g('object_transfer')), _clamp(g('bag_on_ground_before'))
    asym = 1 - mutual
    contact_f = min(contact / 1.0, 1)
    pair = 1.0 if people >= 2 else 0.2          # play/hug/sport need a second person
    p = {}
    # THREAT
    p['forceful_contact'] = 0.10 + 0.40 * min(peak / 3, 1) + 0.20 * min(contact / 2, 1) + 0.30 * fall
    p['victim_distress'] = 0.05 + 0.25 * fall + 0.65 * min(still / 10, 1) + 0.10 * flee + 0.10 * asym
    p['object_taken_forcibly'] = (transfer * (0.45 + 0.35 * flee + 0.15 * asym) * (1 - 0.5 * bag)) + 0.03 + 0.10 * flee * (1 - transfer)
    p['one_sided_aggression'] = 0.05 + 0.45 * asym * contact_f + 0.25 * fall + 0.20 * flee * contact_f + 0.05 * min(peak / 3, 1)
    # BENIGN
    p['play_fighting'] = pair * (0.05 + 0.35 * mutual + 0.30 * alt + 0.20 * min(dur / 4, 1) - 0.35 * fall - 0.25 * flee)
    calm = 1 - min(peak / 2.5, 1)
    p['greeting_or_hug'] = pair * (0.03 + 0.45 * calm * mutual + 0.25 * min(contact / 2, 1) * (1 if contact <= 8 else 0)
                                   + 0.20 * (1 - alt) - 0.40 * fall - 0.30 * flee)
    p['own_belongings'] = 0.03 + bag * (1 - flee) * (0.60 + 0.30 * (1 - asym * transfer))
    p['sport_or_dance'] = 0.03 + 0.45 * alt * (0.5 + 0.5 * mutual) + 0.15 * min(people / 4, 1) + 0.15 * min(dur / 10, 1) - 0.35 * fall - 0.30 * flee
    return {k: round(_clamp(v), 3) for k, v in p.items()}


class RulesGate:
    name = 'rules'
    model = None

    async def answer(self, evidence, questions):
        probs = rules_probabilities(evidence.features)
        return {q.id: probs[q.id] for q in questions}


GATE_SYSTEM = """You assess a possible campus-safety incident from structured kinematic features, optional still images and rule
evidence produced by a camera triage system. For each question, output a calibrated probability that the answer is yes.
Output only probabilities, one entry per question id, no explanation. Detections are uncalibrated and can be wrong; use
moderate probabilities when cues are missing or conflicting. Never infer identity, demographics, intent or guilt: judge only
the observable motion and objects."""


class _Answer(BaseModel):
    id: str
    probability: float


class _Answers(BaseModel):
    answers: list[_Answer]


def _api_key():
    return settings.anthropic_api_key or os.getenv('ANTHROPIC_API_KEY', '')


def _log_usage(model, usage, started, **extra):
    row = {'ts': time.time(), 'model': model, 'latency_ms': round((time.perf_counter() - started) * 1000),
           'input_tokens': getattr(usage, 'input_tokens', None), 'output_tokens': getattr(usage, 'output_tokens', None),
           'cached_tokens': getattr(usage, 'cache_read_input_tokens', None), **extra}
    with open(settings.data_dir / 'debate_usage.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


class ClaudeGate:
    name = 'claude'

    def __init__(self, client=None):
        self._client = client
        self.model = settings.debate_model

    async def answer(self, evidence, questions):
        client = self._client
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=_api_key())
        content: list[dict] = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': b}}
                               for b in evidence.image_b64s[:4]]
        content.append({'type': 'text', 'text': json.dumps({
            'incident_type': evidence.incident_type, 'observation': evidence.observation,
            'rule_evidence': evidence.rule_evidence, 'features': evidence.features,
            'questions': [{'id': q.id, 'text': q.text} for q in questions]})})
        started = time.perf_counter()
        response = await client.beta.messages.parse(
            model=settings.debate_model, max_tokens=600, betas=[FALLBACK_BETA], fallbacks='default',
            output_config={'effort': 'low'}, system=GATE_SYSTEM,
            messages=[{'role': 'user', 'content': content}], output_format=_Answers)
        self.model = getattr(response, 'model', None) or settings.debate_model
        _log_usage(self.model, getattr(response, 'usage', None), started, stop_reason=getattr(response, 'stop_reason', None),
                   questions=[q.id for q in questions])
        if getattr(response, 'stop_reason', None) == 'refusal' or response.parsed_output is None:
            raise RuntimeError('model returned no answers')
        wanted = {q.id for q in questions}
        got = {a.id: _clamp(float(a.probability)) for a in response.parsed_output.answers if a.id in wanted}
        if set(got) != wanted:
            raise ValueError(f'model answered {sorted(got)} but {sorted(wanted)} were asked')
        return {q.id: round(got[q.id], 3) for q in questions}


class JevGate:
    """Placeholder for the "Jev" vendor decision gate. NOT IMPLEMENTED.

    Unverified vendor claims, taken from the proposal and not tested by us: structured JSON state plus images in,
    typed questions answered in parallel, 70-500 ms latency, roughly $0.0004 per decision. We do not know its API,
    so this class calls no endpoint. It exists so a real adapter can be dropped in behind the Gate protocol once the
    vendor API spec is available. Until then selecting it raises NotConfigured and run_debate falls back to rules.
    """
    name = 'jev'
    model = None

    async def answer(self, evidence, questions):
        raise NotConfigured('Jev adapter needs the vendor API spec')


def get_gate() -> Gate:
    choice = settings.debate_gate
    if choice == 'auto':
        choice = 'claude' if _api_key() else 'rules'
    return {'claude': ClaudeGate, 'jev': JevGate}.get(choice, RulesGate)()


# ---- result and brief ----------------------------------------------------------------------

class Brief(BaseModel):
    bullets: list[str] = Field(min_length=3, max_length=3)


class DebateResult(BaseModel):
    source: Literal['rules', 'claude', 'jev']
    model: str | None
    latency_ms: float
    threat: dict[str, float]
    benign: dict[str, float]
    threat_score: float
    benign_score: float
    decision: Literal['escalate', 'downgrade', 'suppress']
    advocate: Literal['lost', 'draw', 'won']
    brief: Brief
    note: str | None = None


def _score(answers: dict[str, float]) -> float:
    if not answers:
        return 0.0
    vals = list(answers.values())
    return round(0.7 * max(vals) + 0.3 * sum(vals) / len(vals), 3)


def decide(threat: float, benign: float) -> tuple[str, str]:
    if threat >= ESCALATE_THREAT and threat - benign >= ESCALATE_MARGIN:
        decision = 'escalate'
    elif threat >= DOWNGRADE_THREAT:
        decision = 'downgrade'
    else:
        decision = 'suppress'
    if decision == 'escalate':
        advocate = 'lost'
    elif decision == 'suppress' or benign >= threat + 0.05:
        advocate = 'won'
    else:
        advocate = 'draw'
    return decision, advocate


def _pct(p):
    return f'{round(p * 100)}%'


def _top(answers: dict[str, float], n=1):
    return sorted(answers.items(), key=lambda kv: -kv[1])[:n]


def _yn(v):
    return 'yes' if v >= 0.5 else 'no'


def _describe(key, v):
    if key in ('fall_after_contact', 'flee_after', 'object_transfer', 'bag_on_ground_before'):
        return f'{key.replace("_", " ")}: {_yn(v)}'
    name = {'contact_seconds': 'contact', 'post_event_still_s': 'still after event', 'duration_s': 'duration'}.get(key, key.replace('_', ' '))
    unit = {'peak_speed': ' body heights/s', 'contact_seconds': ' s', 'post_event_still_s': ' s', 'duration_s': ' s'}.get(key, '')
    return f'{name} {v:g}{unit}'


# per benign question: (feature, high value supports the benign reading, threshold)
_BENIGN_CUES = {
    'play_fighting': [('mutual_motion', True, 0.5), ('motion_alternation', True, 0.5), ('fall_after_contact', False, 0.5), ('flee_after', False, 0.5)],
    'greeting_or_hug': [('peak_speed', False, 1.5), ('mutual_motion', True, 0.5), ('fall_after_contact', False, 0.5), ('flee_after', False, 0.5)],
    'own_belongings': [('bag_on_ground_before', True, 0.5), ('flee_after', False, 0.5), ('object_transfer', False, 0.5)],
    'sport_or_dance': [('motion_alternation', True, 0.5), ('mutual_motion', True, 0.5), ('fall_after_contact', False, 0.5), ('flee_after', False, 0.5)],
}


def _cues(qid, features, contradicting):
    out = []
    for key, high_supports, thr in _BENIGN_CUES[qid]:
        if key not in features:
            continue
        v = features[key]
        supports = (v >= thr) if high_supports else (v < thr)
        if supports != contradicting:
            out.append(_describe(key, v))
    return out


def build_brief(ev: DebateEvidence, threat, benign, decision, advocate, note=None) -> Brief:
    f = ev.features
    (tq, tp), *rest = _top(threat, 2)
    b1 = f'Threat agent: {LABEL[tq]} at {_pct(tp)}'
    if rest:
        b1 += f', then {LABEL[rest[0][0]]} at {_pct(rest[0][1])}'
    cues = [_describe(k, f[k]) for k in ('peak_speed', 'contact_seconds', 'fall_after_contact', 'flee_after', 'object_transfer', 'post_event_still_s') if k in f and f[k] >= 0.5]
    b1 += f'. Cues: {"; ".join(cues[:4])}.' if cues else '. No kinematic cues were supplied.'
    bq, bp = _top(benign)[0]
    if advocate == 'lost':
        why = _cues(bq, f, True)
        lead = "Devil's advocate found no credible benign reading" if bp < 0.15 else "Devil's advocate best case"
        b2 = f"{lead}: {LABEL[bq]} at {_pct(bp)}, which lost to the threat reading ({_pct(max(threat.values()))})"
        b2 += f' because {"; ".join(why[:3])}.' if why else '.'
    elif advocate == 'won':
        why = _cues(bq, f, False)
        b2 = f"Devil's advocate best case: {LABEL[bq]} at {_pct(bp)}, and it carries the debate"
        b2 += f' because {"; ".join(why[:3])}.' if why else '.'
        b2 += ' Alert suppressed.' if decision == 'suppress' else ' Alert kept as low priority.'
    else:
        b2 = f"Devil's advocate best case: {LABEL[bq]} at {_pct(bp)}, close to the threat reading, so neither side wins outright."
    missing = [k for k in FEATURE_KEYS if k not in f]
    unc = f'{len(missing)} of {len(FEATURE_KEYS)} motion cues were not available. ' if missing else ''
    verify = {'person_down': 'Verify on video whether the person is responsive and needs medical help.',
              'altercation': 'Verify on video whether contact is mutual and whether anyone is hurt.',
              'rack_activity': 'Verify on video what object moved and whether the person put it down or took it up.',
              'other': 'Verify on video what is happening before dispatching.'}[ev.incident_type]
    b3 = f'{unc}Scores come from detector features, not confirmed facts. {verify}'
    if note:
        b3 += f' ({note})'
    return Brief(bullets=[b1, b2, b3])


# ---- debate --------------------------------------------------------------------------------

async def run_debate(evidence: DebateEvidence, gate: Gate | None = None) -> DebateResult:
    gate = gate or get_gate()
    started = time.perf_counter()
    note = None
    try:
        threat, benign = await asyncio.gather(gate.answer(evidence, THREAT_QS), gate.answer(evidence, BENIGN_QS))
        _check(threat, THREAT_QS)
        _check(benign, BENIGN_QS)
    except Exception as error:  # a gate must never block triage
        detail = str(error) if isinstance(error, NotConfigured) else type(error).__name__
        note = f'{gate.name} gate unavailable ({detail}); rules-based debate.'
        gate = RulesGate()
        threat, benign = await asyncio.gather(gate.answer(evidence, THREAT_QS), gate.answer(evidence, BENIGN_QS))
    ts, bs = _score(threat), _score(benign)
    decision, advocate = decide(ts, bs)
    if not evidence.features and not evidence.image_b64s and gate.name == 'rules':
        # Nothing to debate with: never suppress a flagged event on an empty record. Keep it as low priority.
        decision, advocate = 'downgrade', 'draw'
        note = note or 'No motion cues or images were supplied, so the debate had nothing to weigh; kept as low priority.'
    brief = build_brief(evidence, threat, benign, decision, advocate, note)
    latency = round((time.perf_counter() - started) * 1000, 2)
    return DebateResult(source=gate.name, model=gate.model, latency_ms=latency, threat=threat, benign=benign,
                        threat_score=ts, benign_score=bs, decision=decision, advocate=advocate, brief=brief, note=note)


def _check(answers, questions):
    if set(answers) != {q.id for q in questions} or any(not 0 <= v <= 1 for v in answers.values()):
        raise ValueError('gate returned invalid answers')
