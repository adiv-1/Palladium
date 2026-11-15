// Adversarial agent debate client. Mirrors apps/api/debate.py.
// localDebate() is a port of RulesGate + run_debate + build_brief and MUST stay in sync with the Python formulas.

export type DebateIncidentType = 'person_down' | 'altercation' | 'rack_activity' | 'other';
export type DebateDecision = 'escalate' | 'downgrade' | 'suppress';
export type AdvocateVerdict = 'lost' | 'draw' | 'won';

export interface DebateEvidence {
  incident_type: DebateIncidentType;
  observation: string;
  rule_evidence?: string[];
  /** people_count, contact_seconds, mutual_motion, motion_alternation, peak_speed, fall_after_contact, flee_after,
   *  object_transfer, post_event_still_s, duration_s, bag_on_ground_before (all optional). */
  features?: Record<string, number>;
  image_b64s?: string[];
}

export interface DebateResult {
  source: 'rules' | 'claude' | 'jev';
  model: string | null;
  latency_ms: number;
  threat: Record<string, number>;
  benign: Record<string, number>;
  threat_score: number;
  benign_score: number;
  decision: DebateDecision;
  advocate: AdvocateVerdict;
  brief: {bullets: string[]};
  note: string | null;
  /** Client-side flag: the features came from a staged demo, not a detector. */
  simulated?: boolean;
}

export const THREAT_IDS = ['forceful_contact', 'victim_distress', 'object_taken_forcibly', 'one_sided_aggression'] as const;
export const BENIGN_IDS = ['play_fighting', 'greeting_or_hug', 'own_belongings', 'sport_or_dance'] as const;

export const LABEL: Record<string, string> = {
  forceful_contact: 'forceful contact', victim_distress: 'person hurt or down', object_taken_forcibly: 'object taken forcibly',
  one_sided_aggression: 'one-sided aggression', play_fighting: 'play-fighting', greeting_or_hug: 'greeting or hug',
  own_belongings: 'picking up own belongings', sport_or_dance: 'sport or dance',
};

const FEATURE_KEYS = ['people_count', 'contact_seconds', 'mutual_motion', 'motion_alternation', 'peak_speed', 'fall_after_contact',
  'flee_after', 'object_transfer', 'post_event_still_s', 'duration_s', 'bag_on_ground_before'];

export async function requestDebate(evidence: DebateEvidence): Promise<DebateResult> {
  try {
    const r = await fetch('/api/debate', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(evidence),
    });
    if (r.ok) return (await r.json()) as DebateResult;
  } catch {
    // network error: use the local rules port
  }
  return localDebate(evidence);
}

// ---- local port of RulesGate (keep in sync with apps/api/debate.py) -------------------------------

const clamp = (x: number) => Math.max(0, Math.min(1, x));
const r3 = (x: number) => Math.round(x * 1000) / 1000;

export function rulesProbabilities(features: Record<string, number>): Record<string, number> {
  const g = (key: string, d = 0) => (features[key] === undefined ? d : Number(features[key]));
  const people = g('people_count', 2), contact = g('contact_seconds');
  const mutual = clamp(g('mutual_motion', 0.5)), alt = clamp(g('motion_alternation', 0.3));
  const peak = g('peak_speed'), still = g('post_event_still_s'), dur = g('duration_s');
  const fall = clamp(g('fall_after_contact')), flee = clamp(g('flee_after'));
  const transfer = clamp(g('object_transfer')), bag = clamp(g('bag_on_ground_before'));
  const asym = 1 - mutual;
  const contactF = Math.min(contact / 1, 1);
  const pair = people >= 2 ? 1 : 0.2;
  const calm = 1 - Math.min(peak / 2.5, 1);
  const p: Record<string, number> = {};
  p.forceful_contact = 0.10 + 0.40 * Math.min(peak / 3, 1) + 0.20 * Math.min(contact / 2, 1) + 0.30 * fall;
  p.victim_distress = 0.05 + 0.25 * fall + 0.65 * Math.min(still / 10, 1) + 0.10 * flee + 0.10 * asym;
  p.object_taken_forcibly = transfer * (0.45 + 0.35 * flee + 0.15 * asym) * (1 - 0.5 * bag) + 0.03 + 0.10 * flee * (1 - transfer);
  p.one_sided_aggression = 0.05 + 0.45 * asym * contactF + 0.25 * fall + 0.20 * flee * contactF + 0.05 * Math.min(peak / 3, 1);
  p.play_fighting = pair * (0.05 + 0.35 * mutual + 0.30 * alt + 0.20 * Math.min(dur / 4, 1) - 0.35 * fall - 0.25 * flee);
  p.greeting_or_hug = pair * (0.03 + 0.45 * calm * mutual + 0.25 * Math.min(contact / 2, 1) * (contact <= 8 ? 1 : 0)
    + 0.20 * (1 - alt) - 0.40 * fall - 0.30 * flee);
  p.own_belongings = 0.03 + bag * (1 - flee) * (0.60 + 0.30 * (1 - asym * transfer));
  p.sport_or_dance = 0.03 + 0.45 * alt * (0.5 + 0.5 * mutual) + 0.15 * Math.min(people / 4, 1) + 0.15 * Math.min(dur / 10, 1)
    - 0.35 * fall - 0.30 * flee;
  for (const k of Object.keys(p)) p[k] = r3(clamp(p[k]));
  return p;
}

const score = (a: Record<string, number>) => {
  const v = Object.values(a);
  return v.length ? r3(0.7 * Math.max(...v) + 0.3 * (v.reduce((s, x) => s + x, 0) / v.length)) : 0;
};

function decide(threat: number, benign: number): [DebateDecision, AdvocateVerdict] {
  const decision: DebateDecision = threat >= 0.55 && threat - benign >= 0.20 ? 'escalate' : threat >= 0.40 ? 'downgrade' : 'suppress';
  const advocate: AdvocateVerdict = decision === 'escalate' ? 'lost' : decision === 'suppress' || benign >= threat + 0.05 ? 'won' : 'draw';
  return [decision, advocate];
}

const pct = (p: number) => `${Math.round(p * 100)}%`;
const top = (a: Record<string, number>, n = 1) => Object.entries(a).sort((x, y) => y[1] - x[1]).slice(0, n);
const yn = (v: number) => (v >= 0.5 ? 'yes' : 'no');

function describe(key: string, v: number) {
  if (['fall_after_contact', 'flee_after', 'object_transfer', 'bag_on_ground_before'].includes(key)) return `${key.replace(/_/g, ' ')}: ${yn(v)}`;
  const names: Record<string, string> = {contact_seconds: 'contact', post_event_still_s: 'still after event', duration_s: 'duration'};
  const units: Record<string, string> = {peak_speed: ' body heights/s', contact_seconds: ' s', post_event_still_s: ' s', duration_s: ' s'};
  return `${names[key] ?? key.replace(/_/g, ' ')} ${v}${units[key] ?? ''}`;
}

// [feature, high value supports the benign reading, threshold]
const BENIGN_CUES: Record<string, [string, boolean, number][]> = {
  play_fighting: [['mutual_motion', true, 0.5], ['motion_alternation', true, 0.5], ['fall_after_contact', false, 0.5], ['flee_after', false, 0.5]],
  greeting_or_hug: [['peak_speed', false, 1.5], ['mutual_motion', true, 0.5], ['fall_after_contact', false, 0.5], ['flee_after', false, 0.5]],
  own_belongings: [['bag_on_ground_before', true, 0.5], ['flee_after', false, 0.5], ['object_transfer', false, 0.5]],
  sport_or_dance: [['motion_alternation', true, 0.5], ['mutual_motion', true, 0.5], ['fall_after_contact', false, 0.5], ['flee_after', false, 0.5]],
};

function cues(qid: string, f: Record<string, number>, contradicting: boolean) {
  const out: string[] = [];
  for (const [key, highSupports, thr] of BENIGN_CUES[qid]) {
    if (f[key] === undefined) continue;
    const supports = highSupports ? f[key] >= thr : f[key] < thr;
    if (supports !== contradicting) out.push(describe(key, f[key]));
  }
  return out;
}

const VERIFY: Record<DebateIncidentType, string> = {
  person_down: 'Verify on video whether the person is responsive and needs medical help.',
  altercation: 'Verify on video whether contact is mutual and whether anyone is hurt.',
  rack_activity: 'Verify on video what object moved and whether the person put it down or took it up.',
  other: 'Verify on video what is happening before dispatching.',
};

function buildBrief(ev: DebateEvidence, threat: Record<string, number>, benign: Record<string, number>, advocate: AdvocateVerdict,
  decision: DebateDecision, note: string | null): string[] {
  const f = ev.features ?? {};
  const [first, second] = top(threat, 2);
  let b1 = `Threat agent: ${LABEL[first[0]]} at ${pct(first[1])}`;
  if (second) b1 += `, then ${LABEL[second[0]]} at ${pct(second[1])}`;
  const cueList = ['peak_speed', 'contact_seconds', 'fall_after_contact', 'flee_after', 'object_transfer', 'post_event_still_s']
    .filter((k) => f[k] !== undefined && f[k] >= 0.5).map((k) => describe(k, f[k]));
  b1 += cueList.length ? `. Cues: ${cueList.slice(0, 4).join('; ')}.` : '. No kinematic cues were supplied.';
  const [bq, bp] = top(benign)[0];
  const maxThreat = Math.max(...Object.values(threat));
  let b2: string;
  if (advocate === 'lost') {
    const why = cues(bq, f, true);
    const lead = bp < 0.15 ? "Devil's advocate found no credible benign reading" : "Devil's advocate best case";
    b2 = `${lead}: ${LABEL[bq]} at ${pct(bp)}, which lost to the threat reading (${pct(maxThreat)})`;
    b2 += why.length ? ` because ${why.slice(0, 3).join('; ')}.` : '.';
  } else if (advocate === 'won') {
    const why = cues(bq, f, false);
    b2 = `Devil's advocate best case: ${LABEL[bq]} at ${pct(bp)}, and it carries the debate`;
    b2 += why.length ? ` because ${why.slice(0, 3).join('; ')}.` : '.';
    b2 += decision === 'suppress' ? ' Alert suppressed.' : ' Alert kept as low priority.';
  } else {
    b2 = `Devil's advocate best case: ${LABEL[bq]} at ${pct(bp)}, close to the threat reading, so neither side wins outright.`;
  }
  const missing = FEATURE_KEYS.filter((k) => f[k] === undefined);
  const unc = missing.length ? `${missing.length} of ${FEATURE_KEYS.length} motion cues were not available. ` : '';
  let b3 = `${unc}Scores come from detector features, not confirmed facts. ${VERIFY[ev.incident_type]}`;
  if (note) b3 += ` (${note})`;
  return [b1, b2, b3];
}

export function localDebate(ev: DebateEvidence): DebateResult {
  const started = performance.now();
  const probs = rulesProbabilities(ev.features ?? {});
  const threat = Object.fromEntries(THREAT_IDS.map((k) => [k, probs[k]]));
  const benign = Object.fromEntries(BENIGN_IDS.map((k) => [k, probs[k]]));
  const ts = score(threat), bs = score(benign);
  let [decision, advocate] = decide(ts, bs);
  let note = 'local fallback';
  if (!Object.keys(ev.features ?? {}).length && !(ev.image_b64s ?? []).length) {
    decision = 'downgrade';
    advocate = 'draw';
    note = 'local fallback; no motion cues or images were supplied, so the debate had nothing to weigh; kept as low priority.';
  }
  return {
    source: 'rules', model: null, latency_ms: Math.round((performance.now() - started) * 100) / 100, threat, benign,
    threat_score: ts, benign_score: bs, decision, advocate, brief: {bullets: buildBrief(ev, threat, benign, advocate, decision, note)}, note,
  };
}

// ---- demo evidence: three real threats and their benign look-alikes -------------------------------

export type DemoVariant = 'real' | 'benign';

/** Simulated features for staging the debate. Real: assault, person down, snatching. Benign: play-fight, hug, own bag. */
export function demoEvidence(type: 'altercation' | 'person_down' | 'rack_activity', variant: DemoVariant): DebateEvidence {
  const table: Record<string, DebateEvidence> = {
    'altercation:real': {
      incident_type: 'altercation', observation: 'Two people in close contact; one moves at the other, who falls and the first leaves.',
      rule_evidence: ['fast contact sample', 'one person on the ground after contact'],
      features: {people_count: 2, contact_seconds: 2.5, mutual_motion: 0.15, motion_alternation: 0.1, peak_speed: 3.4,
        fall_after_contact: 1, flee_after: 1, post_event_still_s: 6, duration_s: 5, bag_on_ground_before: 0},
    },
    'altercation:benign': {
      incident_type: 'altercation', observation: 'Two people in close contact with rapid movement, trading pushes back and forth.',
      rule_evidence: ['fast contact sample'],
      features: {people_count: 2, contact_seconds: 4, mutual_motion: 0.85, motion_alternation: 0.8, peak_speed: 2.2,
        fall_after_contact: 0, flee_after: 0, post_event_still_s: 0, duration_s: 9, bag_on_ground_before: 0},
    },
    'person_down:real': {
      incident_type: 'person_down', observation: 'One person is on the ground and has not moved for over 15 seconds.',
      rule_evidence: ['lying posture', 'no motion for 18 s'],
      features: {people_count: 1, contact_seconds: 0, peak_speed: 1.2, fall_after_contact: 0, post_event_still_s: 18, duration_s: 20},
    },
    'person_down:benign': {
      incident_type: 'person_down', observation: 'Two people close together, one low to the ground, then both upright again.',
      rule_evidence: ['low posture for a few seconds'],
      features: {people_count: 2, contact_seconds: 3.5, mutual_motion: 0.9, motion_alternation: 0.15, peak_speed: 0.8,
        fall_after_contact: 0, flee_after: 0, post_event_still_s: 1.5, duration_s: 6},
    },
    'rack_activity:real': {
      incident_type: 'rack_activity', observation: 'A bike is pulled from the rack by one person, who then leaves quickly.',
      rule_evidence: ['object removed from rack', 'rapid departure'],
      features: {people_count: 2, contact_seconds: 0.8, mutual_motion: 0.2, motion_alternation: 0.1, peak_speed: 3.6,
        object_transfer: 1, flee_after: 1, duration_s: 4, bag_on_ground_before: 0},
    },
    'rack_activity:benign': {
      incident_type: 'rack_activity', observation: 'One person at the rack bends down to pick up a bag that was on the ground.',
      rule_evidence: ['person dwell at rack'],
      features: {people_count: 1, contact_seconds: 0, peak_speed: 0.6, object_transfer: 0, flee_after: 0,
        bag_on_ground_before: 1, duration_s: 5},
    },
  };
  const e = table[`${type}:${variant}`];
  return {...e, rule_evidence: [...(e.rule_evidence ?? [])], features: {...e.features}};
}
