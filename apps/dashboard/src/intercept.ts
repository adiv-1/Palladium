// Dynamic Intercept Vector. Pure and deterministic (seeded rng); no network, no model calls.
//
// HONESTY, read before trusting any number here:
//  - `heading` (the suspect's escape direction) is a SIMULATED input in this demo. In a real deployment it would
//    come from the direction of the camera track. Nothing here estimates it from video.
//  - The prediction is a HEURISTIC, not a learned model: a Monte Carlo of heading-persistent random walks over the
//    real OpenStreetMap walkable street graph. Probabilities are the share of simulated walks that ended in each
//    branch. They are not calibrated likelihoods.
//  - The graph has no street names, so exits are described by compass side and distance only.
//  - Units are assumed to travel at their nominal mode speed on the same graph. No traffic, doors or gates.
import {nearestIndex, type Graph} from './analysis';
import {buildingAt} from './buildings';
import {CAMPUS_CENTER, cameraCone, distance, onCampus, rng} from './sim';
import type {Building, Camera, LngLat, Officer} from './types';

export type InterceptInput = {
  graph: Graph; cameras: Camera[]; buildings: Building[];
  incident: LngLat;
  heading: number; // degrees clockwise from north. SIMULATED in the demo.
  speed?: number; // suspect m/s, default 3.2 (a brisk run)
  horizonS?: number; // default 45
  units: Officer[]; // only status === 'available' are used
  victim: LngLat;
  risk?: Float32Array | null; // optional edge risk (analysis.edgeRisk) to bias the victim route
};

export type Branch = {path: LngLat[]; prob: number; endAt: LngLat; t: number[]}; // t: seconds after the incident per path point
export type ChokePoint = {position: LngLat; node: number; suspectEtaS: number; unit: Officer; unitEtaS: number; marginS: number; path: LngLat[]; pathT: number[]};

export type InterceptPlan = {
  predicted: Branch[]; // top 3, probs sum <= 1
  main: LngLat[];
  exit: {position: LngLat; label: string; distanceM: number} | null;
  cameraHandoff: {cameraId: string; near: string; etaS: number}[];
  chokePoints: ChokePoint[];
  victimRoute: {to: Building; path: LngLat[]; minutes: number; avoidsSuspect: boolean} | null;
  timeline: {tS: number; text: string}[];
  computeMs: number;
  // Extras for rendering (superset of the requested shape).
  horizonS: number;
  incident: LngLat;
  cameraCones: LngLat[][]; // parallel to cameraHandoff
  simulatedHeading: true;
  method: 'heuristic: heading-persistent random walk on the OSM street graph';
};

export const UNIT_SPEED: Record<Officer['mode'], number> = {foot: 1.4, bike: 4.5, cart: 6};
const WALK_SPEED = 1.35; // m/s, same as analysis.planRoute minutes
const WALKS = 300, KAPPA = 3, MIN_MARGIN_S = 5, AVOID_M = 60, MERGE_M = 50;

// ---- small helpers ------------------------------------------------------------------------------
type V = [number, number];
const KX = Math.cos((CAMPUS_CENTER[1] * Math.PI) / 180);
const unit = (v: V): V => {const n = Math.hypot(v[0], v[1]) || 1; return [v[0] / n, v[1] / n];};
const headingV = (deg: number): V => [Math.sin((deg * Math.PI) / 180), Math.cos((deg * Math.PI) / 180)];
const dirOf = (a: LngLat, b: LngLat): V => unit([(b[0] - a[0]) * KX, b[1] - a[1]]);
const lerp = (a: LngLat, b: LngLat, f: number): LngLat => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
const bearingDeg = (a: LngLat, b: LngLat) => {const v = dirOf(a, b); return ((Math.atan2(v[0], v[1]) * 180) / Math.PI + 360) % 360;};
const COMPASS = ['North', 'Northeast', 'East', 'Southeast', 'South', 'Southwest', 'West', 'Northwest'];
const compass = (deg: number) => COMPASS[Math.round(deg / 45) % 8];
const round10 = (m: number) => Math.max(10, Math.round(m / 10) * 10);

class MinHeap {
  private k: number[] = [];
  private v: number[] = [];
  get size() {return this.k.length;}
  push(key: number, val: number) {
    let i = this.k.push(key) - 1;
    this.v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.k[p] <= this.k[i]) break;
      [this.k[p], this.k[i]] = [this.k[i], this.k[p]]; [this.v[p], this.v[i]] = [this.v[i], this.v[p]]; i = p;
    }
  }
  pop(): [number, number] {
    const top: [number, number] = [this.k[0], this.v[0]];
    const key = this.k.pop()!, val = this.v.pop()!;
    if (this.k.length) {
      this.k[0] = key; this.v[0] = val;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < this.k.length && this.k[l] < this.k[m]) m = l;
        if (r < this.k.length && this.k[r] < this.k[m]) m = r;
        if (m === i) break;
        [this.k[m], this.k[i]] = [this.k[i], this.k[m]]; [this.v[m], this.v[i]] = [this.v[i], this.v[m]]; i = m;
      }
    }
    return top;
  }
}

// Single-source Dijkstra over the whole graph. `cost(e, from, to)` is the edge cost, `init` the cost already paid at `s`.
function shortest(g: Graph, s: number, init: number, cost: (e: number, a: number, b: number) => number) {
  const dist = new Float64Array(g.nodes.length).fill(Infinity), prev = new Int32Array(g.nodes.length).fill(-1);
  const heap = new MinHeap();
  dist[s] = init; heap.push(init, s);
  while (heap.size) {
    const [d, u] = heap.pop();
    if (d > dist[u]) continue;
    for (const [v, e] of g.adj[u]) {
      const nd = d + cost(e, u, v);
      if (nd < dist[v]) {dist[v] = nd; prev[v] = u; heap.push(nd, v);}
    }
  }
  return {dist, prev};
}
const trace = (prev: Int32Array, s: number, t: number): number[] => {
  const path = [t];
  while (path[0] !== s && prev[path[0]] >= 0) path.unshift(prev[path[0]]);
  return path[0] === s ? path : [];
};

const inPoly = ([x, y]: LngLat, ring: LngLat[]) => {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
};

// Points along a timed polyline every `step` metres. cum[i] is the distance at pts[i].
function sample(pts: LngLat[], cum: number[], step: number): {p: LngLat; d: number}[] {
  const out: {p: LngLat; d: number}[] = [];
  for (let i = 0; i + 1 < pts.length; i++) {
    const len = cum[i + 1] - cum[i], n = Math.max(1, Math.ceil(len / step));
    for (let k = 0; k < n; k++) out.push({p: lerp(pts[i], pts[i + 1], k / n), d: cum[i] + (len * k) / n});
  }
  out.push({p: pts[pts.length - 1], d: cum[cum.length - 1]});
  return out;
}

// ---- heuristic walk -------------------------------------------------------------------------------
type Walk = {nodes: number[]; cum: number[]; endPoint: LngLat; total: number};

function oneWalk(g: Graph, start: number, startOff: number, head: V, total: number, rand: () => number, greedy: boolean, from = -1, fromD = startOff): Walk {
  const nodes = [start], cum = [startOff], seen = new Set<number>([start]);
  let cur = start, prev = from, d = fromD, dir = head;
  let endPoint = g.nodes[start];
  while (d < total) {
    let opts = g.adj[cur].filter(([v]) => v !== prev);
    if (!opts.length) break; // dead end: stop, never turn straight back
    const fresh = opts.filter(([v]) => !seen.has(v));
    if (fresh.length) opts = fresh;
    const w = opts.map(([v]) => Math.exp(KAPPA * (dirOf(g.nodes[cur], g.nodes[v])[0] * dir[0] + dirOf(g.nodes[cur], g.nodes[v])[1] * dir[1])));
    let pick = 0;
    if (greedy) pick = w.indexOf(Math.max(...w));
    else {
      let x = rand() * w.reduce((a, b) => a + b, 0);
      for (pick = 0; pick < w.length - 1; pick++) if ((x -= w[pick]) <= 0) break;
    }
    const [v, e] = opts[pick], len = g.edges[e][2];
    const ev = dirOf(g.nodes[cur], g.nodes[v]);
    if (d + len >= total) {endPoint = lerp(g.nodes[cur], g.nodes[v], (total - d) / Math.max(len, 1e-6)); d = total; break;}
    // Persistence: keep most of the current direction, blend in the edge just taken and the original escape heading.
    dir = unit([0.55 * dir[0] + 0.25 * ev[0] + 0.2 * head[0], 0.55 * dir[1] + 0.25 * ev[1] + 0.2 * head[1]]);
    d += len; prev = cur; cur = v; seen.add(v); nodes.push(v); cum.push(d); endPoint = g.nodes[v];
  }
  return {nodes, cum, endPoint, total: Math.min(d, total)};
}

function toTimed(g: Graph, incident: LngLat, w: Walk, speed: number): {path: LngLat[]; cum: number[]; t: number[]} {
  const path = [incident, ...w.nodes.map((n) => g.nodes[n])], cum = [0, ...w.cum];
  if (distance(path[path.length - 1], w.endPoint) > 0.5) {path.push(w.endPoint); cum.push(Math.max(w.total, cum[cum.length - 1] + 0.1));}
  return {path, cum, t: cum.map((d) => d / speed)};
}

// ---- main ---------------------------------------------------------------------------------------------
export function planIntercept(input: InterceptInput): InterceptPlan | null {
  const t0 = performance.now();
  const {graph: g, incident, units, victim} = input;
  const speed = input.speed ?? 3.2, horizonS = input.horizonS ?? 45, total = speed * horizonS;
  if (!g.nodes.length) return null;
  const head = headingV(input.heading), rand = rng(1346 + Math.round(input.heading));
  // The OSM graph is sparse in places (edges of 100+ m), so the nearest node can be behind the suspect. Among nodes
  // not much farther than the nearest, enter the graph at the one best aligned with the escape heading.
  const near0 = nearestIndex(g.nodes, incident), d0 = distance(incident, g.nodes[near0]);
  if (d0 > 250) return null; // incident is nowhere near the walkable graph
  let start = near0, bestScore = Infinity;
  g.nodes.forEach((n, i) => {
    const d = distance(incident, n);
    if (d > d0 + 50) return;
    const dv = dirOf(incident, n), score = d - (d > 1 ? 35 * (dv[0] * head[0] + dv[1] * head[1]) : 0);
    if (score < bestScore) [start, bestScore] = [i, score];
  });
  const startOff = distance(incident, g.nodes[start]);

  // 1. Monte Carlo, then cluster endpoints into branches.
  const walks = Array.from({length: WALKS}, () => oneWalk(g, start, startOff, head, total, rand, false));
  const groups = new Map<number, Walk[]>();
  for (const w of walks) {const n = w.nodes[w.nodes.length - 1]; (groups.get(n) ?? groups.set(n, []).get(n)!).push(w);}
  const clusters: {rep: number; walks: Walk[]}[] = [];
  for (const [n, ws] of [...groups].sort((a, b) => b[1].length - a[1].length || a[0] - b[0])) {
    const home = clusters.find((c) => distance(g.nodes[c.rep], g.nodes[n]) <= MERGE_M);
    if (home) home.walks.push(...ws); else clusters.push({rep: n, walks: [...ws]});
  }
  clusters.sort((a, b) => b.walks.length - a.walks.length || a.rep - b.rep);
  const timed = clusters.slice(0, 3).map((c) => {
    const freq = new Map<string, {w: Walk; n: number}>();
    for (const w of c.walks) {const k = w.nodes.join(','); const f = freq.get(k); if (f) f.n++; else freq.set(k, {w, n: 1});}
    const best = [...freq.values()].sort((a, b) => b.n - a.n)[0].w;
    return {prob: c.walks.length / WALKS, walk: best, ...toTimed(g, incident, best, speed)};
  });
  const predicted: Branch[] = timed.map((b) => ({path: b.path, prob: b.prob, endAt: b.walk.endPoint, t: b.t}));
  const main = timed[0];

  // 2. Exit: extend the main branch greedily past the horizon until it leaves campus (labelled as extrapolated).
  const last = main.walk.nodes[main.walk.nodes.length - 1], prevOfLast = main.walk.nodes[main.walk.nodes.length - 2] ?? -1;
  const ext = oneWalk(g, last, 0, head, 900, rand, true, prevOfLast, 0);
  const extPts = [...main.path, ...ext.nodes.slice(1).map((n) => g.nodes[n])];
  let exit: InterceptPlan['exit'] = null, exitPath: LngLat[] = extPts;
  const out = extPts.findIndex((p) => !onCampus(p));
  const nearTxt = (p: LngLat) => `${round10(distance(incident, p))} m from the incident`;
  if (out > 0) {
    const p = extPts[out], dm = distance(incident, p);
    let along = 0;
    for (let i = 1; i <= out; i++) along += distance(extPts[i - 1], extPts[i]);
    const beyond = along > total + 1;
    exit = {position: p, distanceM: dm, label: `${COMPASS[Math.round(bearingDeg(CAMPUS_CENTER, p) / 45) % 8]} campus edge, ${nearTxt(p)}${beyond ? ` (extrapolated, about T+${Math.round(along / speed)} s)` : ''}`};
    exitPath = extPts.slice(0, out + 1);
  } else {
    const p = main.walk.endPoint;
    exit = {position: p, distanceM: distance(incident, p), label: `No campus exit predicted; ${compass(bearingDeg(incident, p)).toLowerCase()} of the incident, ${nearTxt(p)} at T+${horizonS} s`};
  }

  // 3. Camera hand-off: first time the suspect enters each camera cone along the main path.
  const mainSamples = sample(main.path, main.cum, 4);
  const hits: {cam: Camera; etaS: number; cone: LngLat[]}[] = [];
  for (const cam of input.cameras) {
    if (distance(cam.position, incident) > total + cam.range + 40) continue;
    const cone = cameraCone(cam), hit = mainSamples.find((s) => inPoly(s.p, cone));
    if (hit) hits.push({cam, etaS: Math.round(hit.d / speed), cone});
  }
  hits.sort((a, b) => a.etaS - b.etaS || a.cam.id.localeCompare(b.cam.id));
  const handoff = hits.slice(0, 6);

  // 4. Choke points on the main path at 35%..100% of the horizon that a unit can reach first. The OSM graph is sparse
  // (edges of 100 m or more), so candidates are the path nodes plus points every 20 m along each edge; a unit reaches a
  // mid-edge point through whichever end of that edge is quicker. Junctions (degree <= 3) are preferred.
  type Cand = {pos: LngLat; eta: number; a: number; da: number; b: number; db: number; junction: number};
  const cands: Cand[] = [], mn = main.walk.nodes, mc = main.walk.cum;
  for (let i = 0; i < mn.length; i++) {
    const hasNext = i + 1 < mn.length, segEnd = hasNext ? mc[i + 1] : main.walk.total, segLen = segEnd - mc[i];
    const steps = Math.max(1, Math.ceil(segLen / 20));
    for (let k = 0; k < (hasNext ? steps : steps + 1); k++) {
      const f = k / steps, from = g.nodes[mn[i]], to = hasNext ? g.nodes[mn[i + 1]] : main.walk.endPoint;
      const dd = segLen * f, eta = (mc[i] + dd) / speed;
      cands.push({pos: k === 0 ? from : lerp(from, to, f), eta, a: mn[i], da: dd, b: hasNext ? mn[i + 1] : -1, db: segLen - dd, junction: k === 0 ? g.adj[mn[i]].length : 9});
    }
  }
  const timedCands = cands.filter((c) => c.eta >= 0.35 * horizonS && c.eta <= horizonS);
  const avail = units.filter((u) => u.status === 'available');
  const reach = avail.map((u) => {
    const sp = UNIT_SPEED[u.mode], s0 = nearestIndex(g.nodes, u.position);
    const sp1 = shortest(g, s0, distance(u.position, g.nodes[s0]) / sp, (e) => g.edges[e][2] / sp);
    return {u, sp, s0, ...sp1};
  });
  type Opt = {c: Cand; r: typeof reach[number]; unitEta: number; via: number; score: number};
  const options: Opt[] = [];
  for (const c of timedCands) for (const r of reach) {
    const tA = r.dist[c.a] + c.da / r.sp, tB = c.b >= 0 ? r.dist[c.b] + c.db / r.sp : Infinity;
    const unitEta = Math.min(tA, tB), via = tA <= tB ? c.a : c.b;
    if (!Number.isFinite(unitEta) || unitEta + MIN_MARGIN_S > c.eta) continue;
    options.push({c, r, unitEta, via, score: Math.min(c.eta - unitEta, 30) + (c.junction <= 3 ? 12 : 0) + 0.1 * c.eta});
  }
  options.sort((a, b) => b.score - a.score || a.c.eta - b.c.eta || a.r.u.id.localeCompare(b.r.u.id));
  const chosen: Opt[] = [];
  for (const o of options) {
    if (chosen.length >= 2) break;
    if (chosen.some((x) => x.r.u.id === o.r.u.id || distance(x.c.pos, o.c.pos) < 40)) continue;
    chosen.push(o);
  }
  const chokePoints: ChokePoint[] = chosen.map(({c, r, unitEta, via}) => {
    const nodes = trace(r.prev, r.s0, via), path: LngLat[] = [r.u.position, ...nodes.map((n) => g.nodes[n]), c.pos];
    const cum = [0]; for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + distance(path[i - 1], path[i]));
    return {position: c.pos, node: via, suspectEtaS: c.eta, unit: r.u, unitEtaS: unitEta, marginS: c.eta - unitEta,
      path, pathT: cum.map((d) => d / r.sp)};
  });


  // 5. Victim safe haven: nearest 24 h / library / campus-center building, routed away from the predicted suspect path.
  const danger = [...predicted.map((b) => b.path), exitPath].flatMap((p) => sample(p, cumOf(p), 10).map((s) => s.p))
    .filter((p) => distance(p, victim) > 40); // the suspect is leaving the victim, not standing next to them
  const near = new Uint8Array(g.nodes.length);
  g.nodes.forEach((n, i) => {if (danger.some((p) => distance(n, p) <= AVOID_M)) near[i] = 1;});
  const vNode = nearestIndex(g.nodes, victim);
  const pos = input.risk ? [...input.risk].filter((r) => r > 0).sort((a, b) => a - b) : [];
  const rScale = pos.length ? pos[Math.floor(pos.length * 0.9)] || 1 : 1;
  const vCost = shortest(g, vNode, 0, (e, a, b) => g.edges[e][2] * (1 + (input.risk ? 0.5 * Math.min(3, input.risk[e] / rScale) : 0)) + (near[a] || near[b] ? 500 : 0));
  const havens = input.buildings.filter((b) => onCampus(b.position) && (/24:00|00:00-24:00/.test(b.tags.opening_hours || '') || /library|campus center/i.test(b.name)))
    .sort((a, b) => distance(a.position, victim) - distance(b.position, victim)).slice(0, 5);
  let victimRoute: InterceptPlan['victimRoute'] = null, bestCost = Infinity;
  for (const b of havens) {
    const bn = nearestIndex(g.nodes, b.position), c = vCost.dist[bn];
    if (!Number.isFinite(c) || c >= bestCost) continue;
    const nodes = trace(vCost.prev, vNode, bn);
    if (!nodes.length) continue;
    const path: LngLat[] = [victim, ...nodes.map((n) => g.nodes[n]), b.position];
    let meters = 0; for (let i = 1; i < path.length; i++) meters += distance(path[i - 1], path[i]);
    const avoids = !nodes.some((n) => near[n] && distance(g.nodes[n], victim) > 40);
    bestCost = c;
    victimRoute = {to: b, path, minutes: meters / WALK_SPEED / 60, avoidsSuspect: avoids};
  }

  // 6. Timeline.
  const place = buildingAt(input.buildings, incident, 60)?.name;
  const timeline: InterceptPlan['timeline'] = [{tS: 0, text: `Incident confirmed${place ? ` near ${place}` : ''}. Simulated heading ${compass(input.heading).toLowerCase()} (${Math.round(input.heading)} deg).`}];
  if (victimRoute) timeline.push({tS: 0, text: `Safe-haven route to ${victimRoute.to.name}, about ${Math.max(1, Math.round(victimRoute.minutes))} min${victimRoute.avoidsSuspect ? ', away from the predicted suspect path' : ', could not fully avoid the predicted path'}.`});
  for (const h of handoff) timeline.push({tS: h.etaS, text: `Hand off to ${h.cam.id} near ${h.cam.near}${h.cam.watchedLive ? ' (watched live)' : ''}.`});
  for (const c of chokePoints) timeline.push({tS: Math.round(c.unitEtaS), text: `${c.unit.callsign} (${c.unit.mode}) reaches choke point ${Math.round(c.marginS)} s before the suspect.`});
  if (!chokePoints.length) timeline.push({tS: 0, text: avail.length ? 'No available unit can reach the predicted path before the suspect. Escalate to dispatch.' : 'No available units to intercept. Escalate to dispatch.'});
  timeline.push({tS: horizonS, text: `Suspect predicted ${compass(bearingDeg(incident, main.walk.endPoint)).toLowerCase()} of the incident, ${nearTxt(main.walk.endPoint)}.`});
  timeline.sort((a, b) => a.tS - b.tS);

  return {predicted, main: main.path, exit, chokePoints, victimRoute, timeline, horizonS, incident,
    cameraHandoff: handoff.map((h) => ({cameraId: h.cam.id, near: h.cam.near, etaS: h.etaS})), cameraCones: handoff.map((h) => h.cone),
    simulatedHeading: true, method: 'heuristic: heading-persistent random walk on the OSM street graph', computeMs: performance.now() - t0};
}

function cumOf(path: LngLat[]) {
  const cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + distance(path[i - 1], path[i]));
  return cum;
}
