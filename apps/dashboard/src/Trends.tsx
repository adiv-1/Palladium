import {useEffect, useMemo, useRef, useState} from 'react';
import {HexagonLayer} from '@deck.gl/aggregation-layers';
import {TripsLayer} from '@deck.gl/geo-layers';
import {ArcLayer, ColumnLayer, PathLayer, ScatterplotLayer, TextLayer} from '@deck.gl/layers';
import type {Color, Layer, PickingInfo} from '@deck.gl/core';
import {baselinePosts, coverage, COVER_M, edgeRisk, hourLabel, inShift, matchMoves, planPosts, planRoute, SHIFTS, shiftForHour,
  type Past, type Post, type RouteResult, type Shift} from './analysis';
import {Nav} from './Nav';
import {PlaceInput} from './PlaceInput';
import {Tag} from './Panels';
import {Stage, useStage} from './stage';
import {useCampus} from './data';
import {CAMPUS_CENTER, distance} from './sim';
import {INCIDENT_LABEL, type Building, type IncidentType, type LngLat} from './types';

const TYPE_COLOR: Record<IncidentType, Color> = {altercation: [204, 31, 52, 255], person_down: [242, 110, 48, 255], rack_activity: [255, 204, 0, 255], other: [150, 160, 170, 255]};
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const clamp = (v: number) => Math.max(0, Math.min(1, v));
const lerp = (a: LngLat, b: LngLat, t: number): LngLat => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
const pct = (v: number) => `${Math.round(v * 100)}%`;
const TYPES: IncidentType[] = ['altercation', 'person_down', 'rack_activity'];

type Plan = {posts: Post[]; moves: {from: LngLat; to: LngLat; post: number}[]; before: LngLat[]; startedAt: number; cov: {before: number; after: number}};
type Routes = {fastest: RouteResult; safest: RouteResult; startedAt: number; from: Building; to: Building};

export default function Trends() {
  const campus = useCampus();
  const [tab, setTab] = useState<'plan' | 'route'>('plan');
  const [shift, setShift] = useState<Shift>(shiftForHour(new Date().getHours()));
  const [count, setCount] = useState(6);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [from, setFrom] = useState<Building | null>(null);
  const [to, setTo] = useState<Building | null>(null);
  const [caution, setCaution] = useState(6);
  const [routes, setRoutes] = useState<Routes | null>(null);
  const [routeError, setRouteError] = useState('');
  const stage = useStage(!routes);
  const timeRef = useRef(0);
  timeRef.current = stage.time;

  const points = useMemo(() => campus?.past.filter((p) => inShift(p.hour, shift)) ?? [], [campus, shift]);
  const risk = useMemo(() => (campus ? edgeRisk(campus.graph, points, CAMPUS_CENTER) : null), [campus, points]);

  useEffect(() => {
    if (campus && !from) setFrom(campus.buildings.find((b) => b.name.includes('Tutor Campus Center')) ?? null);
  }, [campus, from]);

  const run = (n = count, s = shift) => {
    if (!campus) return;
    const pts = campus.past.filter((p) => inShift(p.hour, s));
    const posts = planPosts(pts, n, CAMPUS_CENTER, campus.graph.nodes);
    const before = baselinePosts(campus.buildings, n);
    const target = posts.map((p) => p.position);
    setPlan({posts, before, moves: matchMoves(before, target), startedAt: timeRef.current,
      cov: {before: coverage(before, pts), after: coverage(target, pts)}});
    setSelected(null);
    stage.flyTo({longitude: CAMPUS_CENTER[0], latitude: CAMPUS_CENTER[1], zoom: 16.1, pitch: 58}, 1400);
    setTimeout(() => {if (posts[0]) select(posts[0]);}, 3600);
  };
  const select = (p: Post) => {setSelected(p.id); stage.flyTo({longitude: p.position[0], latitude: p.position[1], zoom: 17.6, pitch: 60}, 1500);};

  // Re-run automatically when the shift or the number of ambassadors changes after a first run.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {first.current = false; return;}
    if (plan) run();
    if (routes) setRoutes(null);
  }, [shift, count]); // eslint-disable-line react-hooks/exhaustive-deps

  const findRoute = () => {
    if (!campus || !from || !to || !risk) return;
    if (from.id === to.id) {setRouteError('Pick two different places.'); return;}
    const r = planRoute(campus.graph, risk, points, from.position, to.position, caution);
    if (!r) {setRouteError('No walking route found between those places.'); return;}
    setRouteError('');
    setRoutes({...r, startedAt: stage.time, from, to});
    const mid = lerp(from.position, to.position, 0.5);
    stage.flyTo({longitude: mid[0], latitude: mid[1], zoom: 16.6 - Math.min(1.2, distance(from.position, to.position) / 900), pitch: 55}, 1600);
  };
  useEffect(() => {if (routes) setRoutes(null);}, [caution]); // eslint-disable-line react-hooks/exhaustive-deps

  const t = stage.time;
  const layers = useMemo(() => {
    if (!campus) return [];
    const out: Layer[] = [];
    out.push(new HexagonLayer<Past>({id: 'hex', data: points, getPosition: (p) => p.position, getElevationWeight: (p) => p.weight, getColorWeight: (p) => p.weight,
      radius: 34, coverage: 0.82, extruded: true, elevationScale: 0.9, elevationRange: [0, 38], opacity: 0.5, gpuAggregation: false,
      colorRange: [[60, 40, 70], [105, 45, 80], [160, 40, 80], [204, 31, 52], [238, 90, 60], [255, 150, 60]]}) as unknown as Layer);
    out.push(new ScatterplotLayer<Past>({id: 'pts', data: points, getPosition: (p) => p.position, getFillColor: (p) => [...TYPE_COLOR[p.type].slice(0, 3), 110] as unknown as Color,
      radiusUnits: 'meters', getRadius: 2.2, radiusMinPixels: 1.4}) as unknown as Layer);

    if (plan) {
      const age = t - plan.startedAt;
      const prog = (j: number) => ease(clamp((age - j * 0.12) / 2.4));
      const current = plan.moves.map((m, j) => ({...m, pos: lerp(m.from, m.to, prog(j)), p: prog(j)}));
      out.push(new ArcLayer({id: 'glide', data: current, getSourcePosition: (m: any) => m.from, getTargetPosition: (m: any) => m.to, getWidth: 2,
        getSourceColor: [140, 150, 160, 120 * (1 - clamp((age - 3) / 2))], getTargetColor: [255, 204, 0, 220 * (1 - clamp((age - 3) / 2))], greatCircle: false,
        updateTriggers: {getSourceColor: age > 5, getTargetColor: age > 5}} as any) as unknown as Layer);
      out.push(new ScatterplotLayer({id: 'cover', data: current, getPosition: (m: any) => m.pos, radiusUnits: 'meters', stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2,
        getRadius: (m: any) => COVER_M * m.p, getFillColor: (m: any) => [255, 204, 0, 22 + 16 * Math.sin(t * 3 + m.post)], getLineColor: (m: any) => [255, 204, 0, 160 * m.p],
        updateTriggers: {getRadius: t, getFillColor: t, getLineColor: t}} as any) as unknown as Layer);
      out.push(new ColumnLayer({id: 'posts', data: current.filter((m) => m.p > 0.98), getPosition: (m: any) => m.to, radius: 4.5, diskResolution: 14, extruded: true,
        getElevation: (m: any) => 70 + 8 * Math.sin(t * 2.5 + m.post), getFillColor: (m: any) => (m.post === selected ? [255, 235, 140, 255] : [255, 204, 0, 240]), material: false, pickable: true,
        updateTriggers: {getElevation: t, getFillColor: selected}} as any) as unknown as Layer);
      out.push(new TextLayer({id: 'post-rank', data: plan.posts.filter(() => age > 2.6), getPosition: (p: any) => [p.position[0], p.position[1], 92], getText: (p: any) => String(p.rank),
        getSize: 15, getColor: [255, 235, 140, 255], fontFamily: 'Manrope, sans-serif', fontWeight: 800, outlineWidth: 4, outlineColor: [18, 20, 23, 255], fontSettings: {sdf: true}} as any) as unknown as Layer);
      out.push(new ScatterplotLayer({id: 'walkers', data: current, getPosition: (m: any) => m.pos, radiusUnits: 'pixels', getRadius: 6, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2,
        getFillColor: [240, 243, 247, 255], getLineColor: [255, 204, 0, 255], updateTriggers: {getPosition: t}} as any) as unknown as Layer);
    } else if (campus) {
      out.push(new ScatterplotLayer({id: 'baseline', data: baselinePosts(campus.buildings, count), getPosition: (p: LngLat) => p, radiusUnits: 'pixels', getRadius: 6, stroked: true, lineWidthUnits: 'pixels',
        getLineWidth: 2, getFillColor: [150, 160, 170, 220], getLineColor: [30, 34, 40, 255]} as any) as unknown as Layer);
    }

    if (routes) {
      const age = t - routes.startedAt;
      const path = routes.safest.path;
      out.push(new PathLayer({id: 'fast', data: [routes.fastest.path], getPath: (p: LngLat[]) => p, getColor: [200, 205, 212, 120], widthUnits: 'pixels', getWidth: 3, capRounded: true} as any) as unknown as Layer);
      out.push(new PathLayer({id: 'safe', data: [path], getPath: (p: LngLat[]) => p, getColor: [255, 204, 0, 90], widthUnits: 'pixels', getWidth: 8, capRounded: true, jointRounded: true} as any) as unknown as Layer);
      out.push(new TripsLayer({id: 'safe-flow', data: [path], getPath: (p: LngLat[]) => p, getTimestamps: (p: LngLat[]) => p.map((_, i) => i), getColor: [255, 235, 140],
        widthUnits: 'pixels', getWidth: 5, trailLength: 7, fadeTrail: true, capRounded: true, currentTime: (age * 10) % (path.length + 8)} as any) as unknown as Layer);
      const walk = clamp(age / Math.max(6, routes.safest.meters / 40));
      const at = (() => {
        const target = walk * routes.safest.meters;
        let acc = 0;
        for (let i = 0; i + 1 < path.length; i++) {
          const seg = distance(path[i], path[i + 1]);
          if (acc + seg >= target) return lerp(path[i], path[i + 1], seg ? (target - acc) / seg : 0);
          acc += seg;
        }
        return path[path.length - 1];
      })();
      out.push(new ScatterplotLayer({id: 'walker', data: [at], getPosition: (p: LngLat) => p, radiusUnits: 'pixels', getRadius: 8, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3,
        getFillColor: [255, 255, 255, 255], getLineColor: [204, 31, 52, 255], updateTriggers: {getPosition: t}} as any) as unknown as Layer);
      out.push(new ScatterplotLayer({id: 'ends', data: [{p: routes.from.position, c: [95, 179, 168]}, {p: routes.to.position, c: [204, 31, 52]}], getPosition: (d: any) => d.p, radiusUnits: 'pixels',
        getRadius: 8, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3, getFillColor: (d: any) => [...d.c, 255], getLineColor: [255, 255, 255, 255]} as any) as unknown as Layer);
    }
    return out;
  }, [campus, points, plan, routes, selected, t, count]); // eslint-disable-line react-hooks/exhaustive-deps

  const tooltip = ({object, layer}: PickingInfo) => {
    if (!object || layer?.id !== 'posts') return null;
    const post = plan?.posts[(object as any).post];
    return post ? {text: `Post ${post.rank} · ${post.place}`} : null;
  };
  const selectedPost = plan?.posts.find((p) => p.id === selected) ?? null;
  const shiftInfo = SHIFTS[shift];

  return (
    <div className="app">
      {campus && <Stage stage={stage} layers={layers} tooltip={tooltip} onClick={({object, layer}) => {
        if (layer?.id === 'posts' && object) {const p = plan?.posts[(object as any).post]; if (p) select(p);}
      }} />}
      <header className="strip">
        <Nav active="trends" />
        <div className="chips">
          <div className="chip"><span className="chip-label">Past incidents in window</span><strong>{points.length.toLocaleString()}</strong><Tag kind={campus?.historyReal ? 'real' : 'simulated'} /></div>
          <div className="chip"><span className="chip-label">Window</span><strong>{shiftInfo.label} · {shiftInfo.hint}</strong></div>
        </div>
      </header>

      <aside className="panel side">
        {!campus ? <p className="empty">Loading campus map…</p> : (<>
          <div className="seg big" role="tablist">
            <button role="tab" aria-selected={tab === 'plan'} className={tab === 'plan' ? 'on' : ''} onClick={() => setTab('plan')}>Ambassador posts</button>
            <button role="tab" aria-selected={tab === 'route'} className={tab === 'route' ? 'on' : ''} onClick={() => setTab('route')}>Get Somewhere</button>
          </div>
          <div className="field-label">Shift</div>
          <div className="seg">{(Object.keys(SHIFTS) as Shift[]).map((s) => <button key={s} className={shift === s ? 'on' : ''} onClick={() => setShift(s)} title={SHIFTS[s].hint}>{SHIFTS[s].label}</button>)}</div>

          {tab === 'plan' ? (<>
            <div className="field-label">Ambassadors available <b>{count}</b></div>
            <input className="range" type="range" min={3} max={12} value={count} onChange={(e) => setCount(Number(e.target.value))} aria-label="Number of ambassadors" />
            <button className="primary wide-btn" onClick={() => run()}>{plan ? 'Re-run placement' : 'Place ambassadors'}</button>
            <p className="note">Weighted k-means over past incidents in this shift (altercation ×3, person down ×2.5, bike rack ×1), snapped to real walkways.</p>
            {plan && (<>
              <div className="cov">
                <div className="cov-head"><span>Incident risk covered within {COVER_M} m</span><Tag kind={campus.historyReal ? 'real' : 'simulated'} /></div>
                <div className="cov-row"><small>Current posts</small><div className="meter"><i style={{width: pct(plan.cov.before)}} /></div><b>{pct(plan.cov.before)}</b></div>
                <div className="cov-row"><small>Recommended</small><div className="meter gold"><i style={{width: pct(clamp((t - plan.startedAt - 0.4) / 2.6) * plan.cov.after)}} /></div><b>{pct(plan.cov.after)}</b></div>
              </div>
              <div className="field-label">Why these spots</div>
              <ol className="post-list">
                {plan.posts.map((p) => (
                  <li key={p.id}><button className={selected === p.id ? 'on' : ''} onClick={() => select(p)}>
                    <span className="rank">{p.rank}</span>
                    <span className="post-copy"><strong>{p.place}</strong><small>{p.count} past incidents · peak {hourLabel(p.peakHour)}</small></span>
                    <span className="share">{pct(p.share)}</span>
                  </button></li>
                ))}
              </ol>
            </>)}
          </>) : (<>
            <PlaceInput label="From" buildings={campus.buildings} value={from} onPick={setFrom} placeholder="Where are you?" />
            <PlaceInput label="To" buildings={campus.buildings} value={to} onPick={setTo} placeholder="Where are you going? Try “Leavey”" />
            <div className="field-label">Prefer safer routes <b>{caution === 0 ? 'off' : caution < 5 ? 'a little' : caution < 9 ? 'balanced' : 'strongly'}</b></div>
            <input className="range" type="range" min={0} max={12} value={caution} onChange={(e) => setCaution(Number(e.target.value))} aria-label="How strongly to avoid risky streets" />
            <button className="primary wide-btn" onClick={findRoute} disabled={!from || !to}>Find safest route</button>
            {routeError && <p className="error">{routeError}</p>}
            {routes && <RouteCard r={routes} shiftLabel={shiftInfo.label} real={campus.historyReal} onReplay={() => setRoutes({...routes, startedAt: timeRef.current})} />}
            {!routes && <p className="note">Routes follow real OpenStreetMap walkways. Each street segment is penalised by nearby past incidents in the selected shift, so the route bends away from hotspots.</p>}
          </>)}
        </>)}
      </aside>

      {selectedPost && <PostDetail post={selectedPost} real={!!campus?.historyReal} onClose={() => setSelected(null)} />}
      <div className="panel legend-lite">
        {TYPES.map((k) => <span key={k}><i style={{background: `rgb(${TYPE_COLOR[k].slice(0, 3).join(',')})`}} />{INCIDENT_LABEL[k]}</span>)}
        <span><i className="gold" />Ambassador post</span>
      </div>
    </div>
  );
}

function RouteCard({r, shiftLabel, real, onReplay}: {r: Routes; shiftLabel: string; real: boolean; onReplay: () => void}) {
  const f = r.fastest, s = r.safest;
  const same = Math.abs(s.meters - f.meters) < 5;
  const avoided = f.exposure > 0 ? Math.max(0, Math.round((1 - s.exposure / f.exposure) * 100)) : 0;
  const maxE = Math.max(f.exposure, s.exposure, 0.001);
  return (
    <div className="route-card">
      <div className="route-head"><strong>{r.from.name} → {r.to.name}</strong><Tag kind={real ? 'real' : 'simulated'} title="Incident history used for the risk" /></div>
      {[['Fastest', f, 'grey'], ['Safest', s, 'gold']].map(([label, x, tone]: any) => (
        <div className={`route-row ${tone}`} key={label}>
          <span className="swatch" /><b>{label}</b>
          <span>{Math.round(x.meters)} m · {Math.max(1, Math.round(x.minutes))} min</span>
          <span className="near">{x.nearby} past incidents nearby</span>
          <div className="meter"><i style={{width: `${(x.exposure / maxE) * 100}%`}} /></div>
        </div>
      ))}
      <p className="route-sum">{same ? `The fastest route is already the safest for the ${shiftLabel.toLowerCase()} shift.`
        : `Safest adds ${Math.round(s.meters - f.meters)} m (${Math.max(0, Math.round(s.minutes - f.minutes))} min) and cuts exposure to past incidents by ${avoided}%.`}</p>
      <button className="ghost" onClick={onReplay}>Replay walk</button>
    </div>
  );
}

function PostDetail({post, real, onClose}: {post: Post; real: boolean; onClose: () => void}) {
  const maxH = Math.max(1, ...post.hours);
  const total = Math.max(1, post.count);
  return (
    <aside className="panel detail post-detail" key={post.id}>
      <div className="panel-head">
        <div><span className="eyebrow">Recommended post {post.rank}</span><h2>{post.place}</h2><Tag kind={real ? 'real' : 'simulated'} /></div>
        <button className="ghost close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <p className="why">{post.why}</p>
      <h3>Past incidents by type</h3>
      {TYPES.map((k) => (
        <div className="type-row" key={k}><span>{INCIDENT_LABEL[k]}</span><div className="meter"><i style={{width: `${(post.byType[k] / total) * 100}%`, background: `rgb(${TYPE_COLOR[k].slice(0, 3).join(',')})`}} /></div><b>{post.byType[k]}</b></div>
      ))}
      <h3>By hour of day</h3>
      <div className="hours" role="img" aria-label="Incidents by hour of day">
        {post.hours.map((h, i) => <i key={i} title={`${hourLabel(i)}: ${h}`} className={i === post.peakHour ? 'peak' : ''} style={{height: `${Math.max(3, (h / maxH) * 100)}%`}} />)}
      </div>
      <div className="hours-axis"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>11 PM</span></div>
      <h3>Recent events nearby</h3>
      <ul className="events">
        {post.recent.map((e) => (
          <li key={e.id}><i style={{background: `rgb(${TYPE_COLOR[e.type].slice(0, 3).join(',')})`}} />
            <span><strong>{INCIDENT_LABEL[e.type]}</strong><small>{new Date(e.ts).toLocaleDateString([], {month: 'short', day: 'numeric'})} · {hourLabel(e.hour)} · {e.place}</small></span></li>
        ))}
      </ul>
      {!real && <p className="fine">History is simulated until a geocoded DPS log is added at public/osm/history.json.</p>}
    </aside>
  );
}
