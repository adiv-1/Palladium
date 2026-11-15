// What-If wargaming simulator. Pure functions only (no DOM, no import.meta.env) so it runs in a
// web worker and in a node check script.
//
// This is a SCENARIO simulator, not a prediction of crime. Every behavioural number below is an
// assumption that the UI lists. Walkers are synthetic; risk comes from past incidents (simulated
// unless a real history file is present).
import {coverage, COVER_M, edgeRisk, inShift, nearestIndex, planPosts, type Graph, type Past, type Shift} from './analysis';
import {CAMPUS_CENTER, distance, rng} from './sim';
import type {Building, BuildingCategory, LngLat} from './types';

export type SimInput = {graph: Graph; buildings: Building[]; past: Past[]; shift: Shift; boundary: LngLat[]};
export type SimParams = {
  walkers: number;       // late-night trips simulated
  cutPct: number;        // 0..50, % the Lyft boundary shrinks toward its centroid
  offDuty: number;       // ambassadors off duty
  lyftShare: number;     // 0..1, share of in-zone trips that take the free Lyft instead of walking
  baseAmbassadors: number; // ambassadors on duty today
  seed: number;
};
export const DEFAULTS: SimParams = {walkers: 3000, cutPct: 0, offDuty: 0, lyftShare: 0.4, baseAmbassadors: 8, seed: 2026};

export type Metrics = {
  posts: number;
  totalExposure: number;
  coveredShare: number;       // share of walker-risk (walkers x edge risk) within COVER_M of a post
  uncoveredExposure: number;
  uncoveredIndex: number;     // uncovered exposure relative to scenario A (A = 100)
  walkers: number;            // walkers walking in this scenario
  walkersPassing: number;     // walkers whose route passes within COVER_M of a post
};
export type HotEdge = {path: [LngLat, LngLat]; exposure: number; coveredB: boolean; coveredC: boolean};
export type SimResult = {
  params: SimParams;
  A: Metrics; B: Metrics; C: Metrics;
  postsA: LngLat[]; postsB: LngLat[]; postsC: LngLat[];
  extraWalkers: number;       // walkers who walk only because the Lyft boundary was cut
  lyftRiders: {today: number; scenario: number};
  unroutable: number;
  hot: HotEdge[]; maxExposure: number;
  ms: number;
};

// ---- geometry ------------------------------------------------------------------------------
const M = 111320;
const kx = (lat: number) => M * Math.cos((lat * Math.PI) / 180);
const toXY = (p: LngLat, c: LngLat): [number, number] => [(p[0] - c[0]) * kx(c[1]), (p[1] - c[1]) * M];
const fromXY = (x: number, y: number, c: LngLat): LngLat => [c[0] + x / kx(c[1]), c[1] + y / M];

export function pointInRing([x, y]: LngLat, ring: LngLat[]) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

export function ringCentroid(ring: LngLat[]): LngLat {
  const pts = ring.map((p) => toXY(p, CAMPUS_CENTER));
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length], f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  return Math.abs(a) < 1e-9 ? ring[0] : fromXY(cx / (3 * a), cy / (3 * a), CAMPUS_CENTER);
}

// Shrink toward the centroid in metric space; cutPct 25 keeps 75% of the linear size.
export function shrinkRing(ring: LngLat[], cutPct: number): LngLat[] {
  const c = ringCentroid(ring), s = 1 - cutPct / 100;
  return ring.map((p) => {
    const [x, y] = toXY(p, c);
    return fromXY(x * s, y * s, c);
  });
}

export function ringAreaKm2(ring: LngLat[]) {
  const pts = ring.map((p) => toXY(p, CAMPUS_CENTER));
  let a = 0;
  for (let i = 0; i < pts.length; i++) {const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length]; a += x0 * y1 - x1 * y0;}
  return Math.abs(a) / 2 / 1e6;
}

export function parseBoundary(geo: any): LngLat[] {
  const g = geo?.geometry ?? geo?.features?.[0]?.geometry ?? geo;
  const ring = g?.type === 'MultiPolygon' ? g.coordinates[0][0] : g?.coordinates?.[0];
  return (ring ?? []) as LngLat[];
}

// ---- shortest paths -----------------------------------------------------------------------------
class Heap {
  private k: number[] = [];
  private v: number[] = [];
  get size() {return this.k.length;}
  push(key: number, val: number) {
    let i = this.k.push(key) - 1;
    this.v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.k[p] <= this.k[i]) break;
      [this.k[p], this.k[i]] = [this.k[i], this.k[p]]; [this.v[p], this.v[i]] = [this.v[i], this.v[p]];
      i = p;
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
        [this.k[m], this.k[i]] = [this.k[i], this.k[m]]; [this.v[m], this.v[i]] = [this.v[i], this.v[m]];
        i = m;
      }
    }
    return top;
  }
}

// Single-source shortest-path tree (edge to the parent of every node), cached with an LRU keyed by
// source node. One tree serves every trip leaving that node, so thousands of trips share ~100 runs.
export class Router {
  private cache = new Map<number, Int32Array>();
  hits = 0; misses = 0;
  constructor(private g: Graph, private capacity = 400) {}
  tree(s: number): Int32Array {
    const hit = this.cache.get(s);
    if (hit) {this.hits++; this.cache.delete(s); this.cache.set(s, hit); return hit;}
    this.misses++;
    const {nodes, edges, adj} = this.g, dist = new Float64Array(nodes.length).fill(Infinity), prevEdge = new Int32Array(nodes.length).fill(-1);
    const heap = new Heap();
    dist[s] = 0; heap.push(0, s);
    while (heap.size) {
      const [d, u] = heap.pop();
      if (d > dist[u]) continue;
      for (const [v, e] of adj[u]) {
        const nd = d + edges[e][2];
        if (nd < dist[v]) {dist[v] = nd; prevEdge[v] = e; heap.push(nd, v);}
      }
    }
    this.cache.set(s, prevEdge);
    if (this.cache.size > this.capacity) this.cache.delete(this.cache.keys().next().value as number);
    return prevEdge;
  }
  // Edge ids from s to t, or null when t is unreachable.
  path(s: number, t: number): number[] | null {
    if (s === t) return [];
    const prev = this.tree(s), out: number[] = [];
    let u = t;
    while (u !== s) {
      const e = prev[u];
      if (e < 0) return null;
      out.push(e);
      const [a, b] = this.g.edges[e];
      u = a === u ? b : a;
    }
    return out.reverse();
  }
}

// ---- trip model assumptions ------------------------------------------------------------------------
// Relative weight of a building as the START of a late-night walk, and as the END of one.
const ORIGIN_WEIGHT: Record<BuildingCategory, number> = {library: 5, academic: 1.6, other: 0.7, recreation: 1.2, dining: 0.8, health: 0.3, residence: 2.5, parking: 0.3};
const DEST_WEIGHT: Record<string, number> = {dormitory: 1.5, apartments: 1, residential: 1};
// Off-campus origins (houses, apartments, bars, bus stops) are modelled as street-network nodes
// within STREET_RADIUS_M of campus; the OSM building file only covers the campus core.
export const STREET_ORIGIN_SHARE = 0.3;
const STREET_RADIUS_M = 1500;
const SNAP_M = 150; // a building farther than this from the walking graph is ignored

function weightedPicker(weights: number[], random: () => number) {
  const cum: number[] = [];
  weights.reduce((a, w, i) => (cum[i] = a + w), 0);
  const total = cum[cum.length - 1] || 1;
  return () => {
    const x = random() * total;
    let lo = 0, hi = cum.length - 1;
    while (lo < hi) {const mid = (lo + hi) >> 1; if (cum[mid] < x) lo = mid + 1; else hi = mid;}
    return lo;
  };
}

export type Progress = (fraction: number, label: string) => void;

export function simulate(input: SimInput, params: SimParams, progress: Progress = () => {}, router?: Router): SimResult {
  const t0 = performance.now();
  const {graph, buildings, boundary} = input;
  const {walkers, cutPct, offDuty, lyftShare, baseAmbassadors, seed} = params;
  const oldRing = boundary, newRing = shrinkRing(boundary, cutPct);
  const route = router ?? new Router(graph);
  const pastIn = input.past.filter((p) => inShift(p.hour, input.shift));
  const risk = edgeRisk(graph, pastIn, CAMPUS_CENTER);
  const mids: LngLat[] = graph.edges.map(([a, b]) => [(graph.nodes[a][0] + graph.nodes[b][0]) / 2, (graph.nodes[a][1] + graph.nodes[b][1]) / 2]);

  // Snap buildings to the walking graph once.
  const snapped = buildings.map((b) => {
    const node = nearestIndex(graph.nodes, b.position);
    return {b, node, ok: distance(graph.nodes[node], b.position) <= SNAP_M};
  }).filter((x) => x.ok);
  const bldgOrigins = snapped.map((x) => ({node: x.node, pos: x.b.position, w: ORIGIN_WEIGHT[x.b.category] ?? 0.7}));
  const streetOrigins = graph.nodes.map((pos, node) => ({node, pos, w: 1})).filter((o) => distance(o.pos, CAMPUS_CENTER) <= STREET_RADIUS_M);
  const dests = snapped.filter((x) => x.b.category === 'residence' || (x.b.tags.building ?? '') in DEST_WEIGHT);
  if (!bldgOrigins.length || !streetOrigins.length || !dests.length || boundary.length < 3) throw new Error('Not enough data to simulate (origins, destinations or boundary missing).');
  const zone = (list: {pos: LngLat}[], ring: LngLat[]) => list.map((o) => pointInRing(o.pos, ring));
  const inOldB = zone(bldgOrigins, oldRing), inNewB = zone(bldgOrigins, newRing), inOldS = zone(streetOrigins, oldRing), inNewS = zone(streetOrigins, newRing);

  // Trips are drawn with a fixed seed and fixed draw order, so every scenario shares the same
  // synthetic population and only the parameters differ (common random numbers).
  const random = rng(seed);
  const pickB = weightedPicker(bldgOrigins.map((o) => o.w), random);
  const pickD = weightedPicker(dests.map((d) => DEST_WEIGHT[d.b.tags.building ?? ''] ?? 1.5), random);
  const nE = graph.edges.length, cntA = new Float32Array(nE), cntS = new Float32Array(nE);
  const trips: {path: number[]; walkA: boolean; walkS: boolean}[] = [];
  let unroutable = 0, lyftToday = 0, lyftScenario = 0, extra = 0;
  for (let i = 0; i < walkers; i++) {
    const street = random() < STREET_ORIGIN_SHARE;
    const oi = street ? Math.floor(random() * streetOrigins.length) : pickB();
    const origin = street ? streetOrigins[oi] : bldgOrigins[oi];
    let di = pickD();
    for (let tries = 0; tries < 5 && dests[di].node === origin.node; tries++) di = pickD();
    const u = random();
    const oldIn = street ? inOldS[oi] : inOldB[oi], newIn = street ? inNewS[oi] : inNewB[oi];
    const wantsLyftToday = oldIn && u < lyftShare, wantsLyftNow = newIn && u < lyftShare;
    if (wantsLyftToday) lyftToday++;
    if (wantsLyftNow) lyftScenario++;
    const wA = !wantsLyftToday, wS = wA || !wantsLyftNow; // S walkers are a superset of A walkers
    if (!wS) continue;
    const path = route.path(dests[di].node, origin.node) // undirected graph: trees are rooted at the few destinations;
    if (!path) {unroutable++; continue;}
    trips.push({path, walkA: wA, walkS: wS});
    if (wA) for (const e of path) cntA[e]++;
    for (const e of path) cntS[e]++;
    if (!wA) extra++;
    if ((i & 255) === 0) progress(0.85 * (i / walkers), `Routing walkers ${i.toLocaleString()} of ${walkers.toLocaleString()}`);
  }

  const exposure = (cnt: Float32Array) => cnt.map((c, e) => c * risk[e]);
  const expA = exposure(cntA), expS = exposure(cntS);
  const exposurePoints = (exp: Float32Array): Past[] => {
    const out: Past[] = [];
    exp.forEach((x, e) => {if (x > 0) out.push({id: e, position: mids[e], hour: 0, type: 'other', ts: 0, place: '', weight: x});});
    return out;
  };
  const ptsA = exposurePoints(expA), ptsS = exposurePoints(expS);

  progress(0.88, 'Planning ambassador posts for today');
  const postsA = planPosts(ptsA, baseAmbassadors, CAMPUS_CENTER, graph.nodes).map((p) => p.position);
  // Off-duty ambassadors are drawn at random with their own seed: no hand-picking of who leaves.
  const pickRandom = rng(seed + 7), order = postsA.map((_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {const j = Math.floor(pickRandom() * (i + 1)); [order[i], order[j]] = [order[j], order[i]];}
  const gone = new Set(order.slice(0, Math.min(offDuty, postsA.length)));
  const postsB = postsA.filter((_, i) => !gone.has(i));
  progress(0.95, 'Re-planning with fewer ambassadors');
  const countC = Math.max(0, baseAmbassadors - offDuty);
  const postsC = countC ? planPosts(ptsS, countC, CAMPUS_CENTER, graph.nodes).map((p) => p.position) : [];

  const nearPost = (posts: LngLat[]) => {
    const near = new Uint8Array(nE);
    for (let e = 0; e < nE; e++) for (const q of posts) if (distance(q, mids[e]) <= COVER_M) {near[e] = 1; break;}
    return near;
  };
  const measure = (posts: LngLat[], exp: Float32Array, pts: Past[], near: Uint8Array, pick: (t: {walkA: boolean; walkS: boolean}) => boolean): Metrics => {
    const total = exp.reduce((a, b) => a + b, 0), share = coverage(posts, pts);
    let walkersN = 0, passing = 0;
    for (const t of trips) {
      if (!pick(t)) continue;
      walkersN++;
      if (t.path.some((e) => near[e])) passing++;
    }
    return {posts: posts.length, totalExposure: total, coveredShare: share, uncoveredExposure: total * (1 - share), uncoveredIndex: 0, walkers: walkersN, walkersPassing: passing};
  };
  const nearA = nearPost(postsA), nearB = nearPost(postsB), nearC = nearPost(postsC);
  const A = measure(postsA, expA, ptsA, nearA, (t) => t.walkA);
  const B = measure(postsB, expS, ptsS, nearB, (t) => t.walkS);
  const C = measure(postsC, expS, ptsS, nearC, (t) => t.walkS);
  for (const m of [A, B, C]) m.uncoveredIndex = A.uncoveredExposure > 0 ? (100 * m.uncoveredExposure) / A.uncoveredExposure : 0;

  const hot = ptsS.sort((a, b) => b.weight - a.weight).slice(0, 900).map((p): HotEdge => {
    const [a, b] = graph.edges[p.id];
    return {path: [graph.nodes[a], graph.nodes[b]], exposure: p.weight, coveredB: !!nearB[p.id], coveredC: !!nearC[p.id]};
  });
  progress(1, 'Done');
  return {params, A, B, C, postsA, postsB, postsC, extraWalkers: extra, lyftRiders: {today: lyftToday, scenario: lyftScenario}, unroutable,
    hot, maxExposure: hot[0]?.exposure ?? 0, ms: performance.now() - t0};
}
