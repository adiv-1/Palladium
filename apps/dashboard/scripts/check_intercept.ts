// Run from apps/dashboard: npx --yes tsx scripts/check_intercept.ts
import {readFileSync} from 'node:fs';
import {buildGraph} from '../src/analysis';
import {parseBuildings} from '../src/buildings';
import {planIntercept, type InterceptInput} from '../src/intercept';
import {makeCameras, makeOfficers, offset} from '../src/sim';

const walk = JSON.parse(readFileSync('public/osm/walk.json', 'utf8'));
const buildings = parseBuildings(JSON.parse(readFileSync('public/osm/buildings.json', 'utf8')).buildings);
const graph = buildGraph(walk);
const lyon = buildings.find((b) => b.name.includes('Lyon Recreation Center'))!;
const incident = offset(lyon.position, 10, -8);
const input: InterceptInput = {graph, cameras: makeCameras(buildings), buildings, incident, heading: 270,
  units: makeOfficers(buildings), victim: offset(incident, -2, 3)};

const assert = (ok: boolean, msg: string) => {if (!ok) {console.error('FAIL: ' + msg); process.exit(1);} console.log('ok   ' + msg);};
const a = planIntercept(input), b = planIntercept(input);
assert(!!a && !!b, 'plan not null');
const strip = (p: typeof a) => JSON.stringify({...p, computeMs: 0});
assert(strip(a) === strip(b), 'deterministic across two runs');
const plan = a!;
console.log(`graph ${graph.nodes.length} nodes, ${graph.edges.length} edges; ${input.cameras.length} cameras, ${input.units.length} units`);
console.log('branches', plan.predicted.map((p) => `${(p.prob * 100).toFixed(0)}% end ${p.endAt.map((x) => x.toFixed(5))}`).join(' | '), 'sum', plan.predicted.reduce((s, p) => s + p.prob, 0).toFixed(2));
console.log('exit:', plan.exit?.label);
assert(plan.predicted.length >= 1 && plan.predicted.reduce((s, p) => s + p.prob, 0) <= 1.0001, 'branch probabilities sum <= 1');
for (const c of plan.chokePoints) {
  console.log(`choke ${c.unit.callsign} (${c.unit.mode}) unit ${c.unitEtaS.toFixed(1)} s, suspect ${c.suspectEtaS.toFixed(1)} s, margin ${c.marginS.toFixed(1)} s`);
  assert(c.unitEtaS + 5 <= c.suspectEtaS + 1e-9, `${c.unit.callsign}: unit ETA + 5 <= suspect ETA`);
}
assert(new Set(plan.chokePoints.map((c) => c.unit.id)).size === plan.chokePoints.length, 'distinct units');
console.log(`choke points: ${plan.chokePoints.length}; camera hand-offs: ${plan.cameraHandoff.map((c) => `${c.cameraId}@${c.etaS}s`).join(', ') || 'none'}`);
assert(!!plan.victimRoute, 'victim route found');
console.log(`victim -> ${plan.victimRoute?.to.name}, ${plan.victimRoute?.minutes.toFixed(1)} min, avoidsSuspect=${plan.victimRoute?.avoidsSuspect}`);
plan.timeline.forEach((e) => console.log(`  T+${e.tS}s ${e.text}`));
console.log(`computeMs ${plan.computeMs.toFixed(1)}`);

// Heading sweep from the same spot: every chosen choke point must beat the suspect by 5 s, and at least one heading must yield one.
let swept = 0;
for (const h of [0, 45, 90, 135, 180, 225, 270, 315]) {
  const p = planIntercept({...input, heading: h})!;
  p.chokePoints.forEach((c) => assert(c.unitEtaS + 5 <= c.suspectEtaS + 1e-9, `heading ${h}: ${c.unit.callsign} margin ${c.marginS.toFixed(1)} s`));
  swept += p.chokePoints.length;
  console.log(`heading ${h}: branches ${p.predicted.map((x) => Math.round(x.prob * 100) + '%').join('/')}, choke ${p.chokePoints.length}, handoff ${p.cameraHandoff.length}, ${p.computeMs.toFixed(1)} ms`);
}
assert(swept > 0, 'heading sweep produced at least one choke point');
// No units available: must say so.
const none = planIntercept({...input, units: input.units.map((u) => ({...u, status: 'en_route' as const}))})!;
assert(none.chokePoints.length === 0 && none.timeline.some((e) => /No available unit/.test(e.text)), 'no units -> empty choke points with a timeline note');
console.log('ALL OK');
