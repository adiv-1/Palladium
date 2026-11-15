// 200 m geofenced alerts and the incident report. Delivery to people is simulated; the report
// fields are hard-coded placeholders for testing the alert flow (the vision pipeline does not
// estimate them, and the project deliberately never infers identity or demographics).
import type {AlertMsg} from './bus';
import {INCIDENT_LABEL, type Building, type Incident, type LngLat, type Officer} from './types';
import {distance, offset, onCampus, rng} from './sim';

export const ALERT_RADIUS_M = 200;

export type AppUser = {id: string; position: LngLat};

// Simulated student-app users scattered around campus (seeded, so the demo is repeatable).
export function makeAppUsers(buildings: Building[], count = 150): AppUser[] {
  const pool = buildings.filter((b) => onCampus(b.position));
  if (!pool.length) return [];
  const random = rng(808);
  return Array.from({length: count}, (_, i) => {
    const b = pool[Math.floor(random() * pool.length)];
    const a = random() * Math.PI * 2, r = 8 + random() * 55;
    return {id: `user-${i + 1}`, position: offset(b.position, Math.cos(a) * r, Math.sin(a) * r)};
  });
}

export const usersWithin = (users: AppUser[], center: LngLat, radius = ALERT_RADIUS_M) => users.filter((u) => distance(u.position, center) <= radius);

const hash = (s: string) => [...s].reduce((h, c) => (Math.imul(h, 31) + c.charCodeAt(0)) >>> 0, 7);
const pick = <T,>(list: T[], seed: number, salt: number) => list[(seed + salt * 7) % list.length];

export type Report = {
  id: string; filedAt: number; type: string; severity: string; location: string; address?: string; camera: string;
  individuals: {label: string; height: string; build: string; clothing: string; heading: string}[];
  summary: string; unitsAssigned: string[]; status: string; placeholder: true;
};

const HEIGHTS = ['5′6″ to 5′8″', '5′8″ to 5′10″', '5′9″ to 5′11″', '5′10″ to 6′0″', '6′0″ to 6′2″'];
const BUILDS = ['slim', 'medium', 'athletic', 'stocky'];
const CLOTHES = ['dark hoodie and blue jeans', 'grey sweatshirt and black pants', 'black jacket and khaki shorts', 'red t-shirt and dark jeans', 'navy jacket and grey joggers'];
const HEADINGS = ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'];

export function makeReport(incident: Incident, building: Building | undefined, units: Officer[], at = Date.now()): Report {
  const seed = hash(incident.id);
  const count = incident.type === 'altercation' ? 2 : 1;
  const address = building ? [building.tags['addr:housenumber'], building.tags['addr:street']].filter(Boolean).join(' ') : '';
  return {
    id: `SC-${new Date(at).toISOString().slice(0, 10).replaceAll('-', '')}-${String(seed % 9000 + 1000)}`, filedAt: at,
    type: INCIDENT_LABEL[incident.type], severity: incident.severity.replaceAll('_', ' ').toLowerCase(), location: building?.name ?? incident.place,
    address: address || undefined, camera: incident.cameraId,
    individuals: Array.from({length: count}, (_, i) => ({label: `Person ${'AB'[i]}`, height: pick(HEIGHTS, seed, i + 1), build: pick(BUILDS, seed, i + 2),
      clothing: pick(CLOTHES, seed, i + 3), heading: pick(HEADINGS, seed, i + 4)})),
    summary: incident.observation, unitsAssigned: units.map((u) => u.callsign), status: 'Awaiting DPS response', placeholder: true,
  };
}

export type ActiveAlert = {msg: AlertMsg; report: Report; startedAt: number; officers: string[]; users: {id: string; position: LngLat; inside: boolean}[]};

export function composeAlert(incident: Incident, building: Building | undefined, officers: Officer[], users: AppUser[], units: Officer[], time: number, at = Date.now()): ActiveAlert {
  const place = building?.name ?? incident.place;
  const inside = users.map((u) => ({...u, inside: distance(u.position, incident.position) <= ALERT_RADIUS_M}));
  return {
    startedAt: time, officers: officers.map((o) => o.callsign), users: inside, report: makeReport(incident, building, units, at),
    msg: {id: `alert-${incident.id}`, kind: 'alert', title: `Safety alert near ${place}`, center: incident.position, radius: ALERT_RADIUS_M, at, place, incidentType: incident.type,
      message: `${INCIDENT_LABEL[incident.type]} reported near ${place}. DPS units are responding. If you are within ${ALERT_RADIUS_M} m, avoid the area and move to a staffed, well-lit building. In an emergency, call DPS.`},
  };
}
