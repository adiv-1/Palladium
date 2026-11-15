// Client for the backend dispatch agent. Falls back to a local rules plan with the same
// shape when SafeSC is not connected, so the demo still works offline (labelled on screen).
import {distance, etaSeconds} from './sim';
import {POLICY, proposeUnits} from './dispatch';
import {INCIDENT_LABEL} from './types';
import type {Building, Incident, Officer} from './types';

export type Urgency = 'critical' | 'high' | 'moderate' | 'low';

export type AgentUnit = {unit_id: string; callsign: string; role: string; reason: string; message: string};

export type Recommendation = {
  recommendation_id: string;
  source: 'agent' | 'rules';
  model?: string;
  urgency: Urgency;
  priority: string;
  overview: string;
  units: AgentUnit[];
  support: string[];
  cautions: string[];
  latency_ms?: number;
  note?: string;
};

const URGENCY: Record<Incident['type'], Urgency> = {
  altercation: 'critical', person_down: 'high', rack_activity: 'low', other: 'moderate',
};

function buildingInfo(b: Building | null) {
  if (!b) return null;
  const address = [b.tags['addr:housenumber'], b.tags['addr:street']].filter(Boolean).join(' ') || null;
  return {name: b.name, address, floors: b.tags['building:levels'] ?? null};
}

function localPlan(incident: Incident, building: Building | null, officers: Officer[]): Recommendation {
  const policy = POLICY[incident.type];
  const picks = proposeUnits(officers, incident).filter((p) => p.recommended);
  const where = building?.name ?? incident.place;
  const label = INCIDENT_LABEL[incident.type];
  return {
    recommendation_id: `local-${Date.now()}`,
    source: 'rules',
    urgency: URGENCY[incident.type],
    priority: policy.priority,
    overview: `${label} near ${where}. ${policy.reason} ${picks.length ? `Closest available: ${picks.map((p) => `${p.officer.callsign} (~${Math.round(p.eta)} s)`).join(', ')}.` : 'No units available.'}`,
    units: picks.map((p, n) => ({
      unit_id: p.officer.id, callsign: p.officer.callsign, role: n === 0 ? 'primary' : 'backup',
      reason: `Nearest available ${p.officer.mode} unit, about ${Math.round(p.eta)} s away.`,
      message: `${p.officer.callsign}: ${policy.priority} ${label.toLowerCase()} at ${where}. ${incident.observation}. Respond and report on arrival.`,
    })),
    support: policy.support,
    cautions: incident.uncertainties.slice(0, 2),
    note: 'Dispatch agent offline (connect SafeSC); local rules plan',
  };
}

export async function requestRecommendation(incident: Incident, building: Building | null, officers: Officer[]): Promise<Recommendation> {
  const policy = POLICY[incident.type];
  const body = {
    incident: {
      id: incident.id, type: incident.type, severity: incident.severity, place: incident.place,
      observation: incident.observation, evidence: incident.evidence, uncertainties: incident.uncertainties,
      position: incident.position, building: buildingInfo(building),
    },
    units: officers.map((o) => ({id: o.id, callsign: o.callsign, mode: o.mode, status: o.status, position: o.position,
      eta_seconds: Math.round(etaSeconds(o, incident.position)), distance_m: Math.round(distance(o.position, incident.position))})),
    policy_hint: {priority: policy.priority, units: policy.units, reason: policy.reason},
  };
  try {
    const r = await fetch('/api/dispatch/recommend', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!r.ok) throw new Error(String(r.status));
    const rec = (await r.json()) as Recommendation;
    if (!rec || !Array.isArray(rec.units)) throw new Error('bad response');
    return rec;
  } catch {
    return localPlan(incident, building, officers);
  }
}

export async function approvePlan(rec: Recommendation, unitIds: string[], note: string): Promise<Record<string, string>> {
  const local = () => Object.fromEntries(rec.units.filter((u) => unitIds.includes(u.unit_id)).map((u) => [u.unit_id, u.message]));
  if (rec.recommendation_id.startsWith('local-')) return local();
  try {
    const r = await fetch('/api/dispatch/approve', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({recommendation_id: rec.recommendation_id, approved_unit_ids: unitIds, note})});
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    const messages: Record<string, string> = local();
    for (const u of data.units || []) if (u.unit_id && u.message) messages[u.unit_id] = u.message;
    return messages;
  } catch {
    return local();
  }
}
