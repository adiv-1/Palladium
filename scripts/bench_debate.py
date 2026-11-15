"""Latency benchmark for the adversarial debate: 300 debates with RulesGate (and ClaudeGate if a key is set).

Run from the repo root: uv run python -m scripts.bench_debate   (or: PYTHONPATH=. uv run python scripts/bench_debate.py)
"""
import asyncio
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.api import debate  # noqa: E402
from apps.api.config import settings  # noqa: E402

N = 300
CASES = [
    ('altercation', dict(people_count=2, contact_seconds=2.5, mutual_motion=0.15, motion_alternation=0.1, peak_speed=3.4, fall_after_contact=1, flee_after=1, duration_s=5)),
    ('altercation', dict(people_count=2, contact_seconds=4, mutual_motion=0.85, motion_alternation=0.8, peak_speed=2.2, duration_s=9)),
    ('rack_activity', dict(people_count=2, contact_seconds=0.8, mutual_motion=0.2, peak_speed=3.6, object_transfer=1, flee_after=1, duration_s=4)),
    ('rack_activity', dict(people_count=1, peak_speed=0.6, bag_on_ground_before=1, duration_s=5)),
    ('person_down', dict(people_count=1, peak_speed=1.2, post_event_still_s=18, duration_s=20)),
]


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]


async def bench(gate_factory, n):
    lat, decisions, fallbacks = [], {}, 0
    for i in range(n):
        kind, feats = CASES[i % len(CASES)]
        r = await debate.run_debate(debate.DebateEvidence(incident_type=kind, observation='bench', features=feats), gate_factory())
        lat.append(r.latency_ms)
        decisions[r.decision] = decisions.get(r.decision, 0) + 1
        fallbacks += r.note is not None
    return lat, decisions, fallbacks


def report(name, lat, decisions, fallbacks):
    print(f'{name}: n={len(lat)} p50={statistics.median(lat):.2f} ms  p95={pct(lat, .95):.2f} ms  max={max(lat):.2f} ms  '
          f'decisions={decisions} fallbacks={fallbacks}')


if __name__ == '__main__':
    report('RulesGate', *asyncio.run(bench(debate.RulesGate, N)))
    if debate._api_key():
        n = int(os.getenv('BENCH_CLAUDE_N', '20'))
        print(f'ClaudeGate ({settings.debate_model}) real calls, n={n}')
        report('ClaudeGate', *asyncio.run(bench(debate.ClaudeGate, n)))
    else:
        print('ClaudeGate: skipped (no ANTHROPIC_API_KEY)')
