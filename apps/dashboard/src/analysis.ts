// Trend analysis: ambassador placement and safest-route search. Plain algorithms, no model calls.
import {buildingAt} from './buildings';
import {distance, rng} from './sim';
import type {Building, HistoryPoint, IncidentType, LngLat} from './types';

export const TYPE_WEIGHT: Record<IncidentType, number> = {altercation: 3, person_down: 2.5, rack_activity: 1, other: 1};
export const COVER_M = 120; // an ambassador "covers" incidents within this distance

export type Past = {id: number; position: LngLat; hour: number; type: IncidentType; ts: number; place: string; weight: number};

export function stampHistory(points: HistoryPoint[], buildings: Building[]): Past[] {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const random = rng(2026);
  return points.map((p, id) => ({
    id, position: p.position, hour: p.hour, type: p.type, weight: TYPE_WEIGHT[p.type],
    ts: midnight.getTime() - (Math.floor(random() * 90) + 1) * 864e5 + p.hour * 36e5 + Math.floor(random() * 60) * 6e4,
    place: buildingAt(buildings, p.position, 70)?.name ?? 'Campus walkway',
  }));
}

// ---- time windows -------------------------------------------------------------------------
export type Shift = 'day' | 'evening' | 'night' | 'all';
export const SHIFTS: Record<Shift, {label: string; from: number; to: number; hint: string}> = {
  day: {label: 'Day', from: 7, to: 15, hint: '7 AM to 3 PM'},
  evening: {label: 'Evening', from: 15, to: 23, hint: '3 PM to 11 PM'},
  night: {label: 'Night', from: 23, to: 7, hint: '11 PM to 7 AM'},
  all: {label: 'All day', from: 0, to: 24, hint: 'Every hour'},
};
export const inShift = (hour: number, s: Shift) => {
  const {from, to} = SHIFTS[s];
  return s === 'all' || (from < to ? hour >= from && hour < to : hour >= from || hour < to);
};
export const shiftForHour = (h: number): Shift => (h >= 7 && h < 15 ? 'day' : h >= 15 && h < 23 ? 'evening' : 'night');
export const hourLabel = (h: number) => `${h % 12 === 0 ? 12 : h % 12} ${h < 12 ? 'AM' : 'PM'}`;

// ---- planar helpers -------------------------------------------------------------------------
const M = 111320;
const toXY = (p: LngLat, c: LngLat): [number, number] => [(p[0] - c[0]) * M * Math.cos((c[1] * Math.PI) / 180), (p[1] - c[1]) * M];
const fromXY = ([x, y]: [number, number], c: LngLat): LngLat => [c[0] + x / (M * Math.cos((c[1] * Math.PI) / 180)), c[1] + y / M];

export function nearestIndex(points: LngLat[], p: LngLat) {
  const k = Math.cos((p[1] * Math.PI) / 180);
  let best = 0, bestD = Infinity;
  for (let i = 0; i < points.length; i++) {
    const d = ((points[i][0] - p[0]) * k) ** 2 + (points[i][1] - p[1]) ** 2;
    if (d < bestD) [best, bestD] = [i, d];
  }
  return best;
}

// ---- ambassador placement ---------------------------------------------------------------------
export type Post = {
  id: number; rank: number; position: LngLat; count: number; share: number; place: string;
  byType: Record<IncidentType, number>; hours: number[]; peakHour: number; recent: Past[]; why: string;
};

const TYPE_NOUN: Record<IncidentType, string> = {altercation: 'physical altercations', person_down: 'person-down calls', rack_activity: 'bike-rack activity', other: 'other incidents'};

export function coverage(posts: LngLat[], points: Past[], radius = COVER_M) {
  let total = 0, covered = 0;
  for (const p of points) {
    total += p.weight;
    if (posts.some((q) => distance(q, p.position) <= radius)) covered += p.weight;
  }
  return total ? covered / total : 0;
}

export function planPosts(points: Past[], k: number, center: LngLat, walkNodes: LngLat[]): Post[] {
  const n = points.length;
  if (!n) return [];
  k = Math.min(k, n);
  const xy = points.map((p) => toXY(p.position, center)), w = points.map((p) => p.weight);
  // Seed with the densest spot, then k-means++ style: far from existing seeds and heavy.
  let first = 0, bestDensity = -1;
  for (let i = 0; i < n; i += 1) {
    let density = 0;
    for (let j = 0; j < n; j += 1) if ((xy[i][0] - xy[j][0]) ** 2 + (xy[i][1] - xy[j][1]) ** 2 < 100 * 100) density += w[j];
    if (density > bestDensity) [first, bestDensity] = [i, density];
  }
  const cent: [number, number][] = [[...xy[first]]];
  while (cent.length < k) {
    let pick = 0, bestV = -1;
    for (let i = 0; i < n; i++) {
      const d = Math.min(...cent.map((c) => (xy[i][0] - c[0]) ** 2 + (xy[i][1] - c[1]) ** 2));
      if (d * w[i] > bestV) [pick, bestV] = [i, d * w[i]];
    }
    cent.push([...xy[pick]]);
  }
  for (let iter = 0; iter < 15; iter++) {
    const sum = cent.map(() => [0, 0, 0]);
    for (let i = 0; i < n; i++) {
      let c = 0, bd = Infinity;
      cent.forEach((q, ci) => {const d = (xy[i][0] - q[0]) ** 2 + (xy[i][1] - q[1]) ** 2; if (d < bd) [c, bd] = [ci, d];});
      sum[c][0] += xy[i][0] * w[i]; sum[c][1] += xy[i][1] * w[i]; sum[c][2] += w[i];
    }
    sum.forEach((s, ci) => {if (s[2]) cent[ci] = [s[0] / s[2], s[1] / s[2]];});
  }
  const totalWeight = w.reduce((a, b) => a + b, 0);
  const posts = cent.map((c, id): Post => {
    let position = fromXY(c, center);
    if (walkNodes.length) position = walkNodes[nearestIndex(walkNodes, position)]; // stand on a real walkway
    const near = points.filter((p) => distance(position, p.position) <= COVER_M);
    const byType = {altercation: 0, person_down: 0, rack_activity: 0, other: 0} as Record<IncidentType, number>;
    const hours = new Array(24).fill(0);
    near.forEach((p) => {byType[p.type]++; hours[p.hour]++;});
    const peakHour = hours.indexOf(Math.max(...hours));
    const weight = near.reduce((a, p) => a + p.weight, 0);
    const top = (Object.keys(byType) as IncidentType[]).sort((a, b) => byType[b] - byType[a])[0];
    const place = near.length ? (buildingNameNear(near, position)) : 'Campus walkway';
    const pct = Math.round((100 * weight) / totalWeight);
    const topPct = near.length ? Math.round((100 * byType[top]) / near.length) : 0;
    return {id, rank: 0, position, count: near.length, share: weight / totalWeight, place, byType, hours, peakHour,
      recent: [...near].sort((a, b) => b.ts - a.ts).slice(0, 5),
      why: `${near.length} past incidents within ${COVER_M} m, ${pct}% of the risk in this shift. ${topPct}% are ${TYPE_NOUN[top]}, and activity peaks at ${hourLabel(peakHour)}.`};
  });
  posts.sort((a, b) => b.share - a.share);
  posts.forEach((p, i) => (p.rank = i + 1));
  return posts;
}

function buildingNameNear(near: Past[], at: LngLat) {
  const counts = new Map<string, number>();
  near.forEach((p) => counts.set(p.place, (counts.get(p.place) ?? 0) + 1));
  const named = [...counts].filter(([name]) => name !== 'Campus walkway').sort((a, b) => b[1] - a[1]);
  return named.length ? named[0][0] : `Walkway at ${at[1].toFixed(4)}, ${at[0].toFixed(4)}`;
}

// Where ambassadors stand today. Simulated: a spread through the central campus.
const BASELINE = ['Ronald Tutor Campus Center', 'Leavey Library', 'Bovard Administration Building', 'Taper Hall', 'Hoffman Hall',
  'Ginsburg Hall', 'Fertitta Hall', 'Watt Hall', 'Annenberg School for Communication & Journalism', 'Lyon Recreation Center', 'Seeley G. Mudd Building', 'Parkside International Residential College'];
export function baselinePosts(buildings: Building[], k: number): LngLat[] {
  return BASELINE.map((name) => buildings.find((b) => b.name.includes(name.split(' ').slice(0, 2).join(' ')))?.position).filter(Boolean).slice(0, k) as LngLat[];
}

// Move each current ambassador to the nearest unassigned recommended post (greedy).
export function matchMoves(from: LngLat[], to: LngLat[]) {
  const pairs: {i: number; j: number; d: number}[] = [];
  from.forEach((a, i) => to.forEach((b, j) => pairs.push({i, j, d: distance(a, b)})));
  pairs.sort((x, y) => x.d - y.d);
  const usedFrom = new Set<number>(), usedTo = new Set<number>(), moves: {from: LngLat; to: LngLat; post: number}[] = [];
  for (const p of pairs) {
    if (usedFrom.has(p.i) || usedTo.has(p.j)) continue;
    usedFrom.add(p.i); usedTo.add(p.j);
    moves.push({from: from[p.i], to: to[p.j], post: p.j});
  }
  to.forEach((b, j) => {if (!usedTo.has(j)) moves.push({from: from[0] ?? b, to: b, post: j});});
  return moves;
}

// ---- walking graph and safest route -------------------------------------------------------------
export type Graph = {nodes: LngLat[]; edges: [number, number, number][]; adj: [number, number][][]};

export function buildGraph(raw: {nodes: LngLat[]; edges: [number, number, number][]}): Graph {
  const adj: [number, number][][] = raw.nodes.map(() => []);
  raw.edges.forEach(([a, b], e) => {adj[a].push([b, e]); adj[b].push([a, e]);});
  return {nodes: raw.nodes, edges: raw.edges, adj};
}

// Risk density along each edge: past incidents weighted by closeness (gaussian, sigma 45 m).
export function edgeRisk(g: Graph, points: Past[], center: LngLat): Float32Array {
  const cell = 100, grid = new Map<string, number[]>();
  const xy = points.map((p) => toXY(p.position, center));
  xy.forEach(([x, y], i) => {
    const key = `${Math.floor(x / cell)},${Math.floor(y / cell)}`;
    (grid.get(key) ?? grid.set(key, []).get(key)!).push(i);
  });
  const risk = new Float32Array(g.edges.length);
  g.edges.forEach(([a, b], e) => {
    const [ax, ay] = toXY(g.nodes[a], center), [bx, by] = toXY(g.nodes[b], center);
    const mx = (ax + bx) / 2, my = (ay + by) / 2, cx = Math.floor(mx / cell), cy = Math.floor(my / cell);
    let r = 0;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      for (const i of grid.get(`${cx + dx},${cy + dy}`) ?? []) {
        const d2 = (xy[i][0] - mx) ** 2 + (xy[i][1] - my) ** 2;
        if (d2 < 150 * 150) r += points[i].weight * Math.exp(-d2 / (2 * 45 * 45));
      }
    }
    risk[e] = r;
  });
  return risk;
}

class MinHeap {
  private keys: number[] = [];
  private vals: number[] = [];
  get size() {return this.keys.length;}
  push(key: number, val: number) {
    let i = this.keys.push(key) - 1;
    this.vals.push(val);
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (this.keys[parent] <= this.keys[i]) break;
      [this.keys[parent], this.keys[i]] = [this.keys[i], this.keys[parent]];
      [this.vals[parent], this.vals[i]] = [this.vals[i], this.vals[parent]];
      i = parent;
    }
  }
  pop(): [number, number] {
    const top: [number, number] = [this.keys[0], this.vals[0]];
    const key = this.keys.pop()!, val = this.vals.pop()!;
    if (this.keys.length) {
      this.keys[0] = key; this.vals[0] = val;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < this.keys.length && this.keys[l] < this.keys[m]) m = l;
        if (r < this.keys.length && this.keys[r] < this.keys[m]) m = r;
        if (m === i) break;
        [this.keys[m], this.keys[i]] = [this.keys[i], this.keys[m]];
        [this.vals[m], this.vals[i]] = [this.vals[i], this.vals[m]];
        i = m;
      }
    }
    return top;
  }
}

function dijkstra(g: Graph, cost: (e: number) => number, s: number, t: number): number[] | null {
  const dist = new Float64Array(g.nodes.length).fill(Infinity), prev = new Int32Array(g.nodes.length).fill(-1);
  const heap = new MinHeap();
  dist[s] = 0;
  heap.push(0, s);
  while (heap.size) {
    const [d, u] = heap.pop();
    if (d > dist[u]) continue;
    if (u === t) break;
    for (const [v, e] of g.adj[u]) {
      const nd = d + cost(e);
      if (nd < dist[v]) {dist[v] = nd; prev[v] = u; heap.push(nd, v);}
    }
  }
  if (!Number.isFinite(dist[t])) return null;
  const path = [t];
  while (path[0] !== s) path.unshift(prev[path[0]]);
  return path;
}

export type RouteResult = {path: LngLat[]; meters: number; minutes: number; exposure: number; nearby: number};

function summarize(g: Graph, nodePath: number[], from: LngLat, to: LngLat, risk: Float32Array, points: Past[]): RouteResult {
  const edgeOf = new Map<string, number>();
  g.edges.forEach(([a, b], e) => {edgeOf.set(`${a}-${b}`, e); edgeOf.set(`${b}-${a}`, e);});
  const path: LngLat[] = [from, ...nodePath.map((i) => g.nodes[i]), to];
  let meters = distance(from, path[1]) + distance(path[path.length - 2], to), weighted = 0;
  for (let i = 0; i + 1 < nodePath.length; i++) {
    const e = edgeOf.get(`${nodePath[i]}-${nodePath[i + 1]}`)!;
    meters += g.edges[e][2];
    weighted += risk[e] * g.edges[e][2];
  }
  // Sample the route every ~15 m and count past incidents that happened within 60 m of it.
  const samples: LngLat[] = [];
  for (let i = 0; i + 1 < path.length; i++) {
    const len = distance(path[i], path[i + 1]), steps = Math.max(1, Math.round(len / 15));
    for (let s = 0; s < steps; s++) samples.push([path[i][0] + ((path[i + 1][0] - path[i][0]) * s) / steps, path[i][1] + ((path[i + 1][1] - path[i][1]) * s) / steps]);
  }
  const nearby = points.filter((p) => samples.some((s) => distance(s, p.position) <= 60)).length;
  return {path, meters, minutes: meters / 1.35 / 60, exposure: weighted / Math.max(1, meters), nearby};
}

export function planRoute(g: Graph, risk: Float32Array, points: Past[], from: LngLat, to: LngLat, caution: number) {
  const positive = [...risk].filter((r) => r > 0).sort((a, b) => a - b);
  const scale = positive.length ? positive[Math.floor(positive.length * 0.9)] || 1 : 1;
  const s = nearestIndex(g.nodes, from), t = nearestIndex(g.nodes, to);
  const fastest = dijkstra(g, (e) => g.edges[e][2], s, t);
  const safest = dijkstra(g, (e) => g.edges[e][2] * (1 + caution * Math.min(3, risk[e] / scale)), s, t);
  if (!fastest || !safest) return null;
  return {fastest: summarize(g, fastest, from, to, risk, points), safest: summarize(g, safest, from, to, risk, points)};
}

// ---- place search ---------------------------------------------------------------------------------------
export function searchPlaces(buildings: Building[], query: string, limit = 6): Building[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const score = (b: Building) => {
    const name = b.name.toLowerCase(), short = (b.tags.short_name || '').toLowerCase();
    if (name.startsWith(q) || short === q) return 0;
    if (name.split(/\s+/).some((w) => w.startsWith(q))) return 1;
    if (name.includes(q) || (b.tags.alt_name || '').toLowerCase().includes(q)) return 2;
    return 9;
  };
  return buildings.map((b) => ({b, s: score(b)})).filter((x) => x.s < 9).sort((a, b) => a.s - b.s || a.b.name.length - b.b.name.length).slice(0, limit).map((x) => x.b);
}
