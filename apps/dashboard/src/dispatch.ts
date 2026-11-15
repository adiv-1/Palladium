// Dispatch engine: proposes units for an incident, then moves each unit through
// notified -> en route -> on scene -> cleared. Pure functions over plain state so the
// dashboard and the officer phone agree on what happened.
import {distance, etaSeconds, offset} from './sim';
import type {DispatchEvent, DispatchRecord, Incident, IncidentType, LngLat, Officer, Priority, Route} from './types';

// The demo runs travel faster than real life so a judge sees a unit arrive. Shown on screen.
export const DEMO_SPEED = 8;
// A unit with no paired phone acknowledges on its own after this long (marked as simulated).
export const AUTO_ACK_MS = 2500;

type Policy = {priority: Priority; units: number; prefer?: Officer['mode']; reason: string; support: string[]};

export const POLICY: Record<IncidentType, Policy> = {
  altercation: {priority: 'P1', units: 2, reason: 'Possible physical altercation: send two units.', support: []},
  person_down: {priority: 'P1', units: 1, reason: 'Possible medical emergency: nearest unit, and request EMS.', support: ['EMS requested (simulated, nothing is sent)']},
  rack_activity: {priority: 'P3', units: 1, prefer: 'bike', reason: 'Check the bike rack. Ownership cannot be judged from video; approach as a check, not a stop.', support: []},
  other: {priority: 'P2', units: 1, reason: 'Check the location.', support: []},
};

export type Proposal = {officer: Officer; eta: number; recommended: boolean};

export function proposeUnits(officers: Officer[], incident: Incident): Proposal[] {
  const policy = POLICY[incident.type];
  const ranked = officers.filter((o) => o.status === 'available')
    .map((o) => ({officer: o, eta: etaSeconds(o, incident.position)}))
    // A preferred mode counts as 40% closer.
    .sort((a, b) => (a.eta * (a.officer.mode === policy.prefer ? 0.6 : 1)) - (b.eta * (b.officer.mode === policy.prefer ? 0.6 : 1)));
  return ranked.map((r, n) => ({...r, recommended: n < policy.units}));
}

export function routePath(from: LngLat, to: LngLat): LngLat[] {
  // An approximate path, not street routing: a gentle curve between the two points.
  const d = distance(from, to);
  const east = (to[0] - from[0]), north = (to[1] - from[1]);
  const bend = Math.min(40, d * 0.12);
  const len = Math.hypot(east, north) || 1;
  const mid = offset([(from[0] + to[0]) / 2, (from[1] + to[1]) / 2], (-north / len) * bend, (east / len) * bend);
  return Array.from({length: 25}, (_, i) => {
    const t = i / 24, a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, c = t * t;
    return [a * from[0] + b * mid[0] + c * to[0], a * from[1] + b * mid[1] + c * to[1]] as LngLat;
  });
}

export function progress(route: Route, now: number) {
  return Math.max(0, Math.min(1, (now - route.startedAt) / (route.seconds * 1000)));
}

export function pointOnRoute(route: Route, now: number): LngLat {
  const p = progress(route, now) * (route.path.length - 1);
  const i = Math.min(route.path.length - 2, Math.floor(p)), f = p - i;
  const [a, b] = [route.path[i], route.path[i + 1]];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

export function remainingSeconds(route: Route, now: number) {
  return Math.max(0, route.seconds - (now - route.startedAt) / 1000);
}

export type DispatchState = {officers: Officer[]; dispatches: DispatchRecord[]};

const log = (d: DispatchRecord, ...events: DispatchEvent[]): DispatchRecord => ({...d, events: [...d.events, ...events]});
const mapDispatch = (s: DispatchState, id: string, f: (d: DispatchRecord) => DispatchRecord) =>
  s.dispatches.map((d) => (d.id === id ? f(d) : d));

export function createDispatch(s: DispatchState, incident: Incident, unitIds: string[], now: number): {state: DispatchState; dispatch: DispatchRecord} {
  const policy = POLICY[incident.type];
  const id = `dispatch-${now}-${Math.floor(Math.random() * 1e6)}`;
  const units = s.officers.filter((o) => unitIds.includes(o.id) && o.status === 'available');
  const dispatch: DispatchRecord = {
    id, incidentId: incident.id, unitIds: units.map((u) => u.id), priority: policy.priority, reason: policy.reason,
    support: policy.support, createdAt: now, status: 'active',
    events: [{at: now, kind: 'created', note: `${policy.priority} · ${units.length} unit${units.length === 1 ? '' : 's'}`},
      ...policy.support.map((note) => ({at: now, kind: 'support' as const, note})),
      ...units.map((u) => ({at: now, kind: 'notified' as const, unitId: u.id}))],
  };
  const officers = s.officers.map((o) => (dispatch.unitIds.includes(o.id)
    ? {...o, status: 'assigned' as const, dispatchId: id, assignedAt: now, busyWith: incident.id} : o));
  return {state: {officers, dispatches: [...s.dispatches, dispatch]}, dispatch};
}

export function acknowledge(s: DispatchState, unitId: string, to: LngLat, now: number, note?: string): DispatchState {
  const unit = s.officers.find((o) => o.id === unitId);
  if (!unit || unit.status !== 'assigned' || !unit.dispatchId) return s;
  const seconds = Math.max(4, etaSeconds(unit, to) / DEMO_SPEED);
  const route: Route = {from: unit.position, to, path: routePath(unit.position, to), startedAt: now, seconds};
  return {
    officers: s.officers.map((o) => (o.id === unitId ? {...o, status: 'en_route', route} : o)),
    dispatches: mapDispatch(s, unit.dispatchId, (d) => log(d, {at: now, kind: 'acknowledged', unitId, note},
      {at: now, kind: 'en_route', unitId, note: `ETA ${Math.round(etaSeconds(unit, to))} s real time`})),
  };
}

// Moves en-route units and marks arrivals. Returns the same object when nothing changed.
export function tick(s: DispatchState, now: number): {state: DispatchState; arrived: Officer[]} {
  const arrived: Officer[] = [];
  let changed = false;
  const officers = s.officers.map((o) => {
    if (o.status !== 'en_route' || !o.route) return o;
    changed = true;
    if (progress(o.route, now) >= 1) {
      const done = {...o, status: 'on_scene' as const, position: o.route.to};
      arrived.push(done);
      return done;
    }
    return {...o, position: pointOnRoute(o.route, now)};
  });
  if (!changed) return {state: s, arrived};
  let dispatches = s.dispatches;
  for (const unit of arrived) {
    dispatches = dispatches.map((d) => (d.id === unit.dispatchId ? log(d, {at: now, kind: 'on_scene', unitId: unit.id}) : d));
  }
  return {state: {officers, dispatches}, arrived};
}

// Marks a unit on scene early (the officer tapped "Arrived" on the phone).
export function arrive(s: DispatchState, unitId: string, now: number): DispatchState {
  const unit = s.officers.find((o) => o.id === unitId);
  if (!unit || !unit.dispatchId || unit.status === 'on_scene' || unit.status === 'available') return s;
  return {
    officers: s.officers.map((o) => (o.id === unitId ? {...o, status: 'on_scene', position: o.route ? pointOnRoute(o.route, now) : o.position} : o)),
    dispatches: mapDispatch(s, unit.dispatchId, (d) => log(d, {at: now, kind: 'on_scene', unitId, note: 'reported from phone'})),
  };
}

function release(o: Officer): Officer {
  return {...o, status: 'available', dispatchId: undefined, route: undefined, assignedAt: undefined, busyWith: undefined};
}

export function clearUnit(s: DispatchState, unitId: string, now: number, note?: string): DispatchState {
  const unit = s.officers.find((o) => o.id === unitId);
  if (!unit?.dispatchId) return s;
  const dispatchId = unit.dispatchId;
  const officers = s.officers.map((o) => (o.id === unitId ? release(o) : o));
  const stillOut = officers.some((o) => o.dispatchId === dispatchId);
  return {
    officers,
    dispatches: mapDispatch(s, dispatchId, (d) => ({...log(d, {at: now, kind: 'cleared', unitId, note}), status: stillOut ? d.status : 'resolved'})),
  };
}

export function resolve(s: DispatchState, dispatchId: string, now: number): DispatchState {
  let state = s;
  for (const unit of s.officers.filter((o) => o.dispatchId === dispatchId)) state = clearUnit(state, unit.id, now, 'cleared by dispatcher');
  return {...state, dispatches: mapDispatch(state, dispatchId, (d) => ({...d, status: 'resolved'}))};
}

export function cancel(s: DispatchState, dispatchId: string, now: number): DispatchState {
  return {
    officers: s.officers.map((o) => (o.dispatchId === dispatchId ? release({...o, position: o.route ? pointOnRoute(o.route, now) : o.position}) : o)),
    dispatches: mapDispatch(s, dispatchId, (d) => ({...log(d, {at: now, kind: 'cancelled', note: 'cancelled by dispatcher'}), status: 'cancelled'})),
  };
}
