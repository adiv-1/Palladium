// Run from apps/dashboard:  npx --yes tsx scripts/check_whatif.ts
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {buildGraph, stampHistory} from '../src/analysis';
import {parseBuildings} from '../src/buildings';
import {simulatedHistory} from '../src/sim';
import {DEFAULTS, parseBoundary, simulate, type SimInput, type SimResult} from '../src/whatif';

const osm = (f: string) => JSON.parse(readFileSync(resolve(process.cwd(), 'public/osm', f), 'utf8'));
const racks = osm('racks.json').racks.map((x: any) => ({id: x.id, position: [x.lng, x.lat], capacity: x.capacity}));
const buildings = parseBuildings(osm('buildings.json').buildings);
const input: SimInput = {
  graph: buildGraph(osm('walk.json')), buildings, past: stampHistory(simulatedHistory(buildings, racks), buildings),
  shift: 'night', boundary: parseBoundary(osm('lyft-boundary.geojson')),
};

const run = (cutPct: number, offDuty: number, walkers = 5000): SimResult => simulate(input, {...DEFAULTS, walkers, cutPct, offDuty});
const pct = (v: number) => `${(100 * v).toFixed(1)}%`;
function show(label: string, r: SimResult) {
  console.log(`\n== ${label}  (${r.ms.toFixed(0)} ms, ${r.params.walkers} trips, unroutable ${r.unroutable})`);
  console.log(`   walkers A ${r.A.walkers}, scenario ${r.B.walkers}, extra from Lyft cut ${r.extraWalkers}; Lyft riders ${r.lyftRiders.today} -> ${r.lyftRiders.scenario}`);
  for (const [n, m] of [['A Today', r.A], ['B No re-plan', r.B], ['C Re-plan', r.C]] as const)
    console.log(`   ${n.padEnd(13)} posts ${m.posts}  covered ${pct(m.coveredShare).padStart(6)}  uncovered idx ${m.uncoveredIndex.toFixed(0).padStart(4)}  pass a post ${m.walkersPassing}/${m.walkers}  exposure ${m.totalExposure.toFixed(0)}`);
}
let failed = 0;
const assert = (ok: boolean, msg: string) => {console.log(`${ok ? 'PASS' : 'FAIL'}  ${msg}`); if (!ok) failed++;};

const base = run(0, 0), base2 = run(0, 0);
const scen = run(25, 2), scen2 = run(25, 2);
const cutOnly = run(25, 0), offOnly = run(0, 2);
show('cut 0%, off duty 0', base);
show('cut 25%, off duty 2', scen);
show('cut 25%, off duty 0', cutOnly);
show('cut 0%, off duty 2', offOnly);

const strip = (r: SimResult) => JSON.stringify({...r, ms: 0});
assert(strip(base) === strip(base2) && strip(scen) === strip(scen2), 'deterministic across two runs');
assert(scen.B.walkers > base.A.walkers && scen.extraWalkers > 0, `cutting the boundary strictly increases walkers (${base.A.walkers} -> ${scen.B.walkers})`);
assert(offOnly.B.coveredShare <= base.A.coveredShare, `removing ambassadors does not raise covered share without re-plan (${pct(base.A.coveredShare)} -> ${pct(offOnly.B.coveredShare)})`);
assert(scen.B.coveredShare <= cutOnly.B.coveredShare, 'same holds in the cut world (off duty 2 vs 0)');
assert(base.B.coveredShare === base.A.coveredShare, 'no-change scenario: B equals A');
console.log(`\nruntimes ms: ${[base, base2, scen, scen2, cutOnly, offOnly].map((r) => r.ms.toFixed(0)).join(', ')}`);
if (failed) {console.log(`${failed} check(s) failed`); process.exit(1);}
console.log('all checks passed');
