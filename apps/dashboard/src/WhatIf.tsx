import {useEffect, useMemo, useRef, useState} from 'react';
import type {JSX} from 'react';
import {ArcLayer, PathLayer, PolygonLayer, ScatterplotLayer} from '@deck.gl/layers';
import type {Color, Layer} from '@deck.gl/core';
import {COVER_M, matchMoves, type Shift} from './analysis';
import type {Campus} from './data';
import {Tag} from './Panels';
import {distance} from './sim';
import type {LngLat} from './types';
import {DEFAULTS, parseBoundary, ringAreaKm2, shrinkRing, STREET_ORIGIN_SHARE, type HotEdge, type Metrics, type SimParams, type SimResult} from './whatif';
import type {WorkerIn, WorkerOut} from './whatif.worker';
import './whatif.css';

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const clamp = (v: number) => Math.max(0, Math.min(1, v));
const lerp = (a: LngLat, b: LngLat, t: number): LngLat => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
const pct = (v: number) => `${Math.round(v * 100)}%`;
const GOLD: Color = [255, 204, 0, 255], CARDINAL: Color = [204, 31, 52, 255], GREY: Color = [170, 178, 188, 255];
const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

// Dashed outline: PathLayer has no dash support without an extension, so cut the ring into pieces.
function dashed(ring: LngLat[], dash = 110, gap = 70): LngLat[][] {
  const out: LngLat[][] = [], pts = [...ring, ring[0]];
  let on = true, left = dash, cur: LngLat[] = [pts[0]];
  for (let i = 0; i + 1 < pts.length; i++) {
    let a = pts[i];
    const b = pts[i + 1];
    let len = distance(a, b);
    while (len > left) {
      const cut = lerp(a, b, left / len);
      if (on) {cur.push(cut); out.push(cur);} else cur = [cut];
      len -= left; a = cut; on = !on; left = on ? dash : gap;
    }
    left -= len;
    if (on) cur.push(b);
  }
  if (on && cur.length > 1) out.push(cur);
  return out;
}

type Run = {id: number; progress: number; label: string};

export function useWhatIf(campus: Campus | null, shift: Shift, active: boolean, time: number): {panel: JSX.Element; layers: Layer[]; busy: boolean} {
  const [cut, setCut] = useState(DEFAULTS.cutPct);
  const [off, setOff] = useState(DEFAULTS.offDuty);
  const [walkers, setWalkers] = useState(DEFAULTS.walkers);
  const [share, setShare] = useState(Math.round(DEFAULTS.lyftShare * 100));
  const [view, setView] = useState<'B' | 'C'>('C');
  const [boundary, setBoundary] = useState<LngLat[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [result, setResult] = useState<SimResult | null>(null);
  const [error, setError] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const timeRef = useRef(time);
  timeRef.current = time;
  const worker = useRef<Worker | null>(null);
  const runId = useRef(0);

  useEffect(() => {
    if (!active || boundary.length) return;
    fetch(`${import.meta.env.BASE_URL}osm/lyft-boundary.geojson`).then((r) => r.json()).then((g) => setBoundary(parseBoundary(g)))
      .catch(() => setError('Could not load the Lyft boundary file.'));
  }, [active, boundary.length]);

  // Create the worker once the map data and boundary are in, and feed it the data a single time.
  useEffect(() => {
    if (!active || !campus || !boundary.length) return;
    const w = new Worker(new URL('./whatif.worker.ts', import.meta.url), {type: 'module'});
    w.onmessage = (e: MessageEvent<WorkerOut>) => {
      const m = e.data;
      if (m.id !== runId.current) return;
      if (m.type === 'progress') setRun({id: m.id, progress: m.fraction, label: m.label});
      else if (m.type === 'result') {setResult(m.result); setStartedAt(timeRef.current); setRun(null);}
      else {setError(m.message); setRun(null);}
    };
    w.onerror = () => {setError('The simulation worker crashed.'); setRun(null);};
    const init: WorkerIn = {type: 'init', graph: {nodes: campus.graph.nodes, edges: campus.graph.edges}, buildings: campus.buildings, past: campus.past, boundary};
    w.postMessage(init);
    worker.current = w;
    return () => {w.terminate(); worker.current = null;};
  }, [active, campus, boundary]);

  const params: SimParams = {...DEFAULTS, walkers, cutPct: cut, offDuty: off, lyftShare: share / 100};
  const start = () => {
    if (!worker.current || run) return;
    const id = ++runId.current;
    setError('');
    setRun({id, progress: 0, label: 'Starting'});
    const msg: WorkerIn = {type: 'run', id, params, shift};
    worker.current.postMessage(msg);
  };
  const stale = !!result && (result.params.cutPct !== cut || result.params.offDuty !== off || result.params.walkers !== walkers || Math.round(result.params.lyftShare * 100) !== share);

  const oldRing = boundary, newRing = useMemo(() => (boundary.length ? shrinkRing(boundary, cut) : []), [boundary, cut]);
  const oldDashes = useMemo(() => (boundary.length ? dashed(boundary) : []), [boundary]);
  const areas = useMemo(() => (boundary.length ? {old: ringAreaKm2(boundary), now: ringAreaKm2(newRing)} : null), [boundary, newRing]);

  const reduced = useMemo(reducedMotion, []);
  const layers = useMemo(() => {
    if (!active) return [];
    const out: Layer[] = [];
    if (oldRing.length) {
      if (cut > 0) out.push(new PolygonLayer({id: 'wi-lost', data: [{polygon: [oldRing, newRing]}], getPolygon: (d: {polygon: LngLat[][]}) => d.polygon, stroked: false, filled: true,
        getFillColor: [204, 31, 52, 46], pickable: false}) as unknown as Layer);
      out.push(new PathLayer<LngLat[]>({id: 'wi-old', data: oldDashes, getPath: (p) => p, getColor: [190, 196, 205, 210], widthUnits: 'pixels', getWidth: 2.5, capRounded: true}) as unknown as Layer);
      if (cut > 0) out.push(new PathLayer<LngLat[]>({id: 'wi-new', data: [[...newRing, newRing[0]]], getPath: (p) => p, getColor: GOLD, widthUnits: 'pixels', getWidth: 3.5, jointRounded: true, capRounded: true}) as unknown as Layer);
    }
    if (!result) return out;
    const age = reduced ? 99 : time - startedAt, fade = ease(clamp(age / 1.4));
    const hotAll = result.hot, covered = (h: HotEdge) => (view === 'C' ? h.coveredC : h.coveredB);
    const max = Math.max(1e-9, result.maxExposure);
    out.push(new PathLayer<HotEdge>({id: 'wi-covered', data: hotAll.filter(covered), getPath: (h) => h.path, getColor: [255, 204, 0, 70], widthUnits: 'pixels', getWidth: 2, capRounded: true,
      updateTriggers: {data: [view, result.ms]}}) as unknown as Layer);
    out.push(new PathLayer<HotEdge>({id: 'wi-gaps', data: hotAll.filter((h) => !covered(h)), getPath: (h) => h.path, widthUnits: 'pixels', capRounded: true, jointRounded: true, opacity: fade,
      getColor: (h) => [204, 31, 52, Math.round(110 + 145 * Math.sqrt(h.exposure / max))] as Color,
      getWidth: (h) => 3 + 12 * Math.sqrt(h.exposure / max), updateTriggers: {data: [view, result.ms]}}) as unknown as Layer);

    out.push(new ScatterplotLayer<LngLat>({id: 'wi-ghost', data: result.postsA, getPosition: (p) => p, radiusUnits: 'pixels', getRadius: 7, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2,
      getFillColor: [150, 160, 170, 90], getLineColor: [150, 160, 170, 200]}) as unknown as Layer);
    const moves = view === 'C' ? matchMoves(result.postsA, result.postsC) : result.postsB.map((p, i) => ({from: p, to: p, post: i}));
    const prog = (j: number) => (reduced ? 1 : ease(clamp((age - 0.5 - j * 0.12) / 2.4)));
    const current = moves.map((m, j) => ({...m, pos: lerp(m.from, m.to, prog(j)), p: prog(j)}));
    if (view === 'C') out.push(new ArcLayer({id: 'wi-glide', data: current.filter((m) => m.p < 1 && distance(m.from, m.to) > 1), getSourcePosition: (m: typeof current[number]) => m.from, getTargetPosition: (m: typeof current[number]) => m.to,
      getWidth: 2, getSourceColor: [140, 150, 160, 120], getTargetColor: [255, 204, 0, 220], updateTriggers: {data: age}} as never) as unknown as Layer);
    out.push(new ScatterplotLayer({id: 'wi-cover', data: current, getPosition: (m: typeof current[number]) => m.pos, radiusUnits: 'meters', stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2,
      getRadius: (m: typeof current[number]) => COVER_M * m.p, getFillColor: (m: typeof current[number]) => [255, 204, 0, Math.round(24 + (reduced ? 0 : 14 * Math.sin(time * 3 + m.post)))] as Color,
      getLineColor: (m: typeof current[number]) => [255, 204, 0, Math.round(170 * m.p)] as Color, updateTriggers: {getRadius: age, getFillColor: time, getLineColor: age, getPosition: age}} as never) as unknown as Layer);
    out.push(new ScatterplotLayer({id: 'wi-posts', data: current, getPosition: (m: typeof current[number]) => m.pos, radiusUnits: 'pixels', getRadius: 7, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2.5,
      getFillColor: GOLD, getLineColor: [18, 20, 23, 255], updateTriggers: {getPosition: age}} as never) as unknown as Layer);
    return out;
  }, [active, oldRing, newRing, oldDashes, cut, result, view, time, startedAt, reduced]);

  const panel = (
    <WhatIfPanel {...{cut, setCut, off, setOff, walkers, setWalkers, share, setShare, view, setView, run, start, result, stale, error, areas, shift, ready: !!worker.current && !!campus, historyReal: !!campus?.historyReal, restartAnim: () => setStartedAt(timeRef.current)}} />
  );
  return {panel, layers, busy: !!run};
}

type PanelProps = {
  cut: number; setCut: (v: number) => void; off: number; setOff: (v: number) => void; walkers: number; setWalkers: (v: number) => void;
  share: number; setShare: (v: number) => void; view: 'B' | 'C'; setView: (v: 'B' | 'C') => void; run: Run | null; start: () => void;
  result: SimResult | null; stale: boolean; error: string; areas: {old: number; now: number} | null; shift: Shift; ready: boolean; historyReal: boolean; restartAnim: () => void;
};

function Slider({label, value, shown, min, max, step, onChange, hint}: {label: string; value: number; shown: string; min: number; max: number; step: number; onChange: (v: number) => void; hint?: string}) {
  return (
    <label className="wi-slider">
      <span className="wi-slider-head"><span>{label}</span><b>{shown}</b></span>
      <input className="range" type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} />
      {hint && <small>{hint}</small>}
    </label>
  );
}

const CARDS: {key: 'A' | 'B' | 'C'; title: string; sub: string; tone: string}[] = [
  {key: 'A', title: 'A · Today', sub: 'Planned for current conditions', tone: 'grey'},
  {key: 'B', title: 'B · Scenario, no re-plan', sub: 'Same posts, fewer ambassadors, new boundary', tone: 'red'},
  {key: 'C', title: 'C · Scenario, SafeSC re-plans', sub: 'Posts re-optimised for the new walkers', tone: 'gold'},
];

function WhatIfPanel(p: PanelProps) {
  const r = p.result, onDuty = DEFAULTS.baseAmbassadors - p.off;
  const lost = r ? r.A.coveredShare - r.B.coveredShare : 0, won = r ? r.C.coveredShare - r.B.coveredShare : 0;
  const summary = !r ? '' : lost <= 0.0005 && won <= 0.0005 ? 'Under these sliders coverage barely changes, so re-planning has little to recover.'
    : `Coverage of walker-risk goes from ${pct(r.A.coveredShare)} today to ${pct(r.B.coveredShare)} if nothing is re-planned. SafeSC re-planning brings it to ${pct(r.C.coveredShare)}, `
      + (lost > 0.0005 ? `recovering ${Math.round((100 * Math.max(0, won)) / lost)}% of the ${(100 * lost).toFixed(1)} points lost.` : `a change of ${(100 * won).toFixed(1)} points.`);
  return (
    <div className="wi">
      <div className="wi-head"><div><span className="wi-eyebrow">Wargaming</span><h2>What-if sandbox</h2></div><Tag kind="simulated" title="Scenario simulator. Not a prediction of crime." /></div>
      <p className="wi-lead">Drag a lever, run thousands of synthetic late-night walks, and see where ambassadors would need to stand.</p>

      <Slider label="Cut the Free Lyft boundary" value={p.cut} shown={`${p.cut}%`} min={0} max={50} step={5} onChange={p.setCut}
        hint={p.areas ? `Zone area ${p.areas.old.toFixed(1)} km² to ${p.areas.now.toFixed(1)} km², shrunk toward its centre. Approximate digitised boundary from the repo docs.` : 'Loading boundary…'} />
      <Slider label="Ambassadors off duty" value={p.off} shown={`${p.off} of ${DEFAULTS.baseAmbassadors} · ${onDuty} on duty`} min={0} max={6} step={1} onChange={p.setOff} />
      <Slider label="Walkers simulated" value={p.walkers} shown={p.walkers.toLocaleString()} min={1000} max={10000} step={500} onChange={p.setWalkers} />
      <Slider label="Lyft usage inside the zone" value={p.share} shown={`${p.share}%`} min={0} max={100} step={5} onChange={p.setShare}
        hint="Assumption: share of in-zone late-night trips that take the free Lyft instead of walking." />

      <button className="primary wide-btn wi-run" onClick={p.start} disabled={!p.ready || !!p.run}>{p.run ? 'Simulating…' : r ? 'Run again' : 'Run simulation'}</button>
      {p.run && (
        <div className="wi-progress" role="progressbar" aria-valuenow={Math.round(p.run.progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div className="wi-bar"><i style={{width: `${Math.round(p.run.progress * 100)}%`}} /></div><small>{p.run.label}</small>
        </div>
      )}
      {p.error && <p className="wi-error">{p.error}</p>}

      {r && (<div className="wi-results" key={r.ms}>
        {p.stale && <p className="wi-stale">Sliders changed since this run. Press Run again to update.</p>}
        <div className="wi-cards">
          {CARDS.map((c, i) => <Card key={c.key} spec={c} m={r[c.key]} delay={i * 0.12} />)}
        </div>
        <p className="wi-summary">{summary}</p>
        <div className="wi-stats">
          <div><b>{r.extraWalkers > 0 ? `+${r.extraWalkers}` : '0'}</b><small>extra walkers from the Lyft cut ({pct(r.A.walkers ? r.extraWalkers / r.A.walkers : 0)})</small></div>
          <div><b>{r.lyftRiders.today} to {r.lyftRiders.scenario}</b><small>simulated Lyft riders</small></div>
          <div><b>{r.params.walkers.toLocaleString()}</b><small>trips in {Math.round(r.ms)} ms</small></div>
        </div>
        <div className="wi-field">Map: show uncovered exposure</div>
        <div className="seg" role="group">
          <button className={p.view === 'B' ? 'on' : ''} onClick={() => {p.setView('B'); p.restartAnim();}}>Without re-plan</button>
          <button className={p.view === 'C' ? 'on' : ''} onClick={() => {p.setView('C'); p.restartAnim();}}>SafeSC re-plans</button>
        </div>
        <ul className="wi-key">
          <li><i className="red" />Uncovered, thicker means more walkers x risk</li><li><i className="gold" />Covered by a post</li>
          <li><i className="grey" />Today's posts (ghosted)</li><li><i className="dash" />Current Lyft boundary</li>{p.cut > 0 && <li><i className="gline" />Cut boundary</li>}
        </ul>
      </div>)}

      <details className="wi-assume" open={!r}>
        <summary>Assumptions</summary>
        <ul>
          <li>This is a scenario simulator, not a prediction of crime. Walkers are synthetic and seeded, so a run is repeatable.</li>
          <li>Each trip starts at a campus building (libraries weighted highest, then residences and academic buildings) or, for {Math.round(STREET_ORIGIN_SHARE * 100)}% of trips, at an off-campus street location within 1.5 km, and ends at a residence. Walkers take the shortest path on the OpenStreetMap walkway graph.</li>
          <li>The Lyft zone is an approximate digitised boundary from the repo docs, not an official geofence. Cutting it shrinks the polygon toward its centroid. Trips starting in the removed ring that would have used Lyft walk instead.</li>
          <li>The Lyft usage share ({p.share}%) is an assumption, not a measured rate. {DEFAULTS.baseAmbassadors} ambassadors are on duty today and the ones going off duty are chosen at random.</li>
          <li>Risk per street segment comes from {p.historyReal ? 'real' : 'simulated'} past incidents in the {p.shift} window. Exposure = walkers on the segment x its risk. Coverage means a segment midpoint within {COVER_M} m of a post.</li>
          <li>Scenario A plans posts for today's walkers; B drops ambassadors and applies the cut without moving anyone; C re-plans with the same weighted k-means as the Ambassador posts tab.</li>
        </ul>
      </details>
    </div>
  );
}

function Card({spec, m, delay}: {spec: typeof CARDS[number]; m: Metrics; delay: number}) {
  const style = {'--d': `${delay}s`} as React.CSSProperties;
  return (
    <div className={`wi-card ${spec.tone}`} style={style}>
      <div className="wi-card-head"><strong>{spec.title}</strong><span>{m.posts} posts</span></div>
      <small>{spec.sub}</small>
      <div className="wi-row"><span>Walker-risk covered</span><b>{pct(m.coveredShare)}</b></div>
      <div className="wi-meter"><i style={{width: pct(m.coveredShare)}} /></div>
      <div className="wi-row"><span>Uncovered exposure index</span><b>{Math.round(m.uncoveredIndex)}</b></div>
      <div className="wi-meter dim"><i style={{width: `${Math.min(100, m.uncoveredIndex / 2.5)}%`}} /></div>
      <div className="wi-row"><span>Walkers passing a post</span><b>{m.walkersPassing.toLocaleString()} <em>of {m.walkers.toLocaleString()}</em></b></div>
    </div>
  );
}
