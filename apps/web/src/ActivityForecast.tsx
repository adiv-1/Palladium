import {useEffect, useMemo, useState} from 'react';
import {ArrowRight, ArrowUpRight, Footprints, MapPin, Navigation, Pause, Play, Shield, X} from 'lucide-react';
import {makeNetwork, routes} from './activityRouting.mjs';
import {campusModel, pointAlongPath} from './campusModel.mjs';
import CampusMap, {type MapFocus} from './CampusMap';
import PlaceSearch from './PlaceSearch';
import {usePlaces} from './campusData';
import {localDate, losAngelesParts, useWorkspaceState, type CampusState, type Place, type Point} from './WorkspaceState';
import {useVisibleAnimation} from './useVisibleAnimation';
import './activityForecast.css';

type Data = Record<string, any>;
const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
const clock = (n: number) => `${String(n).padStart(2, '0')}:00`;
const MODES = [['explore', 'Explore'], ['routes', 'Walking routes'], ['posts', 'Ambassador posts'], ['history', 'History']] as const;

export default function ActivityForecast({bundle, focusMode,onModeChange}: {bundle: Data; focusMode?: 'posts' | 'routes';onModeChange?:(mode:CampusState['mode'])=>void}) {
  const {campus: s, setCampus} = useWorkspaceState(), {places, error: placeError} = usePlaces();
  const patch = (values: Partial<CampusState>) => setCampus(old => ({...old, ...values}));
  useEffect(() => {if (focusMode) setCampus(old => ({...old, mode: focusMode}));}, [focusMode, setCampus]);
  const [picking, setPicking] = useState<'start' | 'end' | null>(null), [focus, setFocus] = useState<MapFocus | null>(null), [historyPage, setHistoryPage] = useState(1);
  const validDate = !!s.date && s.date >= localDate() && s.date <= localDate(7);
  const model = useMemo(() => campusModel(bundle, {...s, date: validDate ? s.date : localDate()}), [bundle, s.date, s.hour, s.family, s.staff, validDate]);
  const {cells, posts} = model;
  const network = useMemo(() => makeNetwork(bundle.graph), [bundle]);
  const route = useMemo(() => {
    if (!s.start || !s.end || !validDate) return {result: null, error: ''};
    try {return {result: routes(network, bundle, s.start.point, s.end.point, s.date, s.hour + s.minute / 60, s.family, s.detour), error: ''};}
    catch (e) {return {result: null, error: (e as Error).message};}
  }, [bundle, network, s.start, s.end, s.date, s.hour, s.minute, s.family, s.detour, validDate]);
  const [replaying, setReplaying] = useState(false);
  useEffect(() => {setReplaying(false);}, [route.result, s.mode]);
  useEffect(()=>{
    if(!route.result)return;
    const points=[...route.result.direct.coordinates,...route.result.alternative.coordinates];
    const a:Point=[Math.min(...points.map((p:Point)=>p[0])),Math.min(...points.map((p:Point)=>p[1]))];
    const b:Point=[Math.max(...points.map((p:Point)=>p[0])),Math.max(...points.map((p:Point)=>p[1]))];
    setFocus({point:a,key:Date.now(),bounds:[a,b]});
  },[route.result]);
  const replay = useVisibleAnimation(replaying, 10000, () => setReplaying(false));
  const replayUnit = replaying && route.result ? pointAlongPath(route.result.alternative.coordinates, replay) : null;
  const history = useMemo(() => bundle.history.points.filter((r: any[]) => r[1] >= s.historyStart && r[1] <= s.historyEnd && (!s.category || r[4] === s.category)).sort((a:any[],b:any[])=>String(b[1]).localeCompare(String(a[1]))), [bundle, s.historyStart, s.historyEnd, s.category]);
  useEffect(() => {setHistoryPage(1);}, [s.historyStart, s.historyEnd, s.category]);
  const report = s.selectedReport ? bundle.history.points.find((r: any[]) => r[0] === s.selectedReport) : null;
  const current = s.selected === null ? null : cells[s.selected]?.properties;
  const selectedPost = s.selectedPost === null ? null : posts[s.selectedPost];
  const areaName = (i: number) => [...new Set(bundle.streets.features.filter((f: Data) => f.properties.cell === i).map((f: Data) => f.properties.name))].filter(n => !String(n).startsWith('Unnamed')).slice(0, 2).join(' / ') || `Area ${bundle.cells[i].id}`;
  const go = (point: Point, zoom = 15.5) => setFocus({point, key: Date.now(), zoom});
  const pick = (point: Point) => {
    if (!picking) return;
    patch({[picking]: {id: `point-${point.join(',')}`, name: `Map point · ${point[1].toFixed(4)}, ${point[0].toFixed(4)}`, point} as Place}); setPicking(null);
  };
  const choosePost = (i: number) => {patch({selectedPost: i, selected: posts[i].cell}); go(posts[i].coordinates);};
  const windowStart = Math.floor(s.hour / 3) * 3;
  const validation = bundle.evaluation.candidates.find((c: Data) => c.config.name === bundle.evaluation.selected);
  const title = s.mode === 'posts' ? 'Ambassador positioning' : s.mode === 'routes' ? 'Get Somewhere' : s.mode === 'history' ? 'Reported history' : 'Campus outlook';
  const endpoints = useMemo(() => [...(s.start ? [{point: s.start.point, label: 'A'}] : []), ...(s.end ? [{point: s.end.point, label: 'B'}] : [])], [s.start, s.end]);
  const covered = useMemo(() => s.mode === 'posts' ? (posts.at(-1)?.covered_cells || []) : [], [s.mode, posts]);

  return <div className="campus-workspace">
    <div className="page-heading"><div><span className="eyebrow">Campus intelligence</span><h1>{title}</h1><p>{s.mode === 'posts' ? 'A limited team. A considered place for every post.' : s.mode === 'routes' ? 'Choose a destination. Compare the walk for your departure time.' : 'Reported activity, with the context to understand it.'}</p></div><span className="status-label"><i className="dot green"/>Public analysis loaded</span></div>
    <div className="campus-toolbar"><div className="segmented" aria-label="Campus sections">{MODES.map(([id, label]) => <button key={id} className={s.mode === id ? 'selected' : ''} aria-pressed={s.mode === id} onClick={() => {patch({mode: id}); setPicking(null);onModeChange?.(id);}}>{label}</button>)}</div><div className="map-options"><div className="segmented" aria-label="Map perspective"><button aria-pressed={!s.threeD} className={!s.threeD ? 'selected' : ''} onClick={() => patch({threeD: false})}>2D</button><button aria-pressed={s.threeD} className={s.threeD ? 'selected' : ''} onClick={() => patch({threeD: true})}>3D</button></div><select aria-label="Map background" value={s.basemap} onChange={e => patch({basemap: e.target.value as CampusState['basemap']})}><option value="streets">Dark campus</option><option value="imagery">Satellite / aerial</option></select></div></div>
    <div className="campus-grid">
      <aside className="campus-panel panel">
        {s.mode !== 'history' ? <>
          <div className="campus-time"><label>Departure date<input type="date" aria-label="Forecast departure date" min={localDate()} max={localDate(7)} value={s.date} onChange={e => patch({date: e.target.value})}/></label><label>Time · Los Angeles<input type="time" aria-label="Exact walking departure time" value={`${String(s.hour).padStart(2, '0')}:${String(s.minute).padStart(2, '0')}`} onChange={e => {if (e.target.value) {const [h, m] = e.target.value.split(':').map(Number); patch({hour: h, minute: m});}}}/></label></div>
          <div className="campus-presets"><button onClick={() => {const p = losAngelesParts(); patch({date: localDate(), hour: +p.hour, minute: +p.minute});}}>Now</button><button onClick={() => patch({date: localDate(), hour: 18, minute: 0})}>Tonight</button><button onClick={() => patch({date: localDate(1)})}>Tomorrow</button></div>
          <label>Report family<select aria-label="Forecast report family" value={s.family} onChange={e => patch({family: e.target.value, selectedPost: null})}>{Object.entries(bundle.families).map(([id, name]) => <option value={id} key={id}>{String(name)}</option>)}</select></label>
          <p className="window-note">{clock(windowStart)}–{clock(windowStart + 3)} seasonal outlook · dates with the same weekday share a forecast.</p>
          {!validDate && <p className="error">Choose a departure from today through seven days ahead.</p>}
        </> : <div className="history-controls"><label>From<input type="date" aria-label="History start date" value={s.historyStart} onChange={e => patch({historyStart: e.target.value, selectedReport: null})}/></label><label>Through<input type="date" aria-label="History end date" value={s.historyEnd} onChange={e => patch({historyEnd: e.target.value, selectedReport: null})}/></label><label>Category<select value={s.category} aria-label="Historical report category" onChange={e => patch({category: e.target.value, selectedReport: null})}><option value="">All categories</option>{Object.keys(bundle.metadata.categories).map(c => <option key={c}>{c}</option>)}</select></label>{s.historyStart > s.historyEnd && <p className="error">Start must be on or before end.</p>}<strong>{history.length.toLocaleString()} grouped public reports</strong><p className="fine">Occurrence dates as reported by LAPD. Approximate locations; classifications can overlap.</p></div>}

        {s.mode === 'routes' && <div className="campus-route-panel">
          <PlaceSearch label="From" value={s.start} places={places} onChange={start => patch({start})}/><button className="text-button" onClick={() => setPicking('start')}><MapPin size={13}/>Choose start on map</button>
          <PlaceSearch label="Destination" value={s.end} places={places} onChange={end => patch({end})}/><button className="text-button" onClick={() => setPicking('end')}><MapPin size={13}/>Choose destination on map</button>
          <label>Extra walking allowed<select aria-label="Walking detour budget" value={s.detour} onChange={e => patch({detour: +e.target.value})}><option value={.15}>Up to 15%</option><option value={.3}>Up to 30%</option><option value={.5}>Up to 50%</option></select></label>
          <p className="fine">Routes update as you choose places or departure time. Named buildings cover central campus; map picking works across the supported zone.</p>
          {placeError && <p className="fine">{placeError}</p>}{route.error && <p className="error" role="alert">{route.error}</p>}
          {route.result ? <div aria-live="polite" className="route-results">{[{value: route.result.direct, label: 'Direct route', kind: 'direct'}, ...(route.result.changed ? [{value: route.result.alternative, label: 'Lower report-activity alternative', kind: 'alternative'}] : [])].map(r => <article className={`route-result ${r.kind}`} key={r.kind}><span className="route-line"/><h3>{r.label}</h3><strong>{r.value.minutes.toFixed(0)} min <small>· {Math.round(r.value.meters)} m</small></strong><p>{r.kind === 'alternative' ? `${pct(1 - r.value.activity / route.result!.direct.activity)} lower modeled report-activity index` : 'Shortest connected walk at an assumed 80 m/min.'}</p><small>{pct(r.value.sparseFraction)} of walking time crosses areas with sparse direct history.</small><details><summary>Streets & paths</summary><p>{r.value.streets.join(' → ')}</p></details></article>)}
            {!route.result.changed && <p className="fine">No lower-activity candidate was found within your detour budget.</p>}
            <button onClick={() => setReplaying(v => !v)}>{replaying ? <Pause size={14}/> : <Play size={14}/>} {replaying ? 'Stop replay' : 'Replay illustrative walk'}</button>
            <p className="fine">Replay speed is illustrative. A/B snap {Math.round(route.result.startSnapMeters)}/{Math.round(route.result.endSnapMeters)} m to the network; off-network walking is excluded. Gates, crossings, lighting and live closures are unverified.</p>
          </div> : <div className="campus-prompt"><Navigation size={23}/><h3>Where are you going?</h3><p>Search a destination or choose two points on the map.</p></div>}
        </div>}

        {s.mode === 'posts' && <div className="campus-post-panel"><label className="staff-control">Ambassadors available <strong>{s.staff}</strong><input type="range" aria-label="Ambassadors available" min={1} max={8} value={s.staff} onChange={e => patch({staff: +e.target.value, selectedPost: null})}/></label>
          <div className="coverage-summary"><span>Modeled activity covered</span><strong>{pct(model.coverage)}</strong><meter min={0} max={1} value={model.coverage}/><small>{model.covered.toFixed(3)} expected reports · {bundle.staffing.radius_m} m network walk · overlap counted once</small></div>
          <h3>Why these spots</h3><ol className="post-list">{posts.map((p: Data, i: number) => <li key={p.cell}><button className={s.selectedPost === i ? 'selected' : ''} onClick={() => choosePost(i)}><b>{i + 1}</b><span>{p.name}<small>+{p.incremental_expected_reports.toFixed(3)} expected report activity covered</small></span><ArrowRight size={14}/></button></li>)}</ol>
          {selectedPost && <div className="post-explanation"><span className="eyebrow">Recommended post {s.selectedPost! + 1}</span><h3>{selectedPost.name}</h3><p>This post adds {selectedPost.incremental_expected_reports.toFixed(3)} expected reports to the scenario’s coverage within a {bundle.staffing.radius_m} m network walk, accounting for earlier posts.</p><small>{selectedPost.covers.length} representative areas within reach. Hypothetical placement, not a current assignment.</small></div>}
          <p className="fine">Ten-year mature history where available, weighted toward recent years and pooled across nearby areas. Scenarios change by weekday, time window and report family. These are planning suggestions, not dispatches.</p>
        </div>}

        {s.mode === 'explore' && <>
          {current ? <div className="area-outlook"><button className="text-button" onClick={() => patch({selected: null})}><X size={13}/>Clear selection</button><h2>{areaName(s.selected!)}</h2><div className="outlook-value">{current.mean.toFixed(3)}<span>expected reports in this area/window</span></div><dl><dt>Modeled chance of at least one report</dt><dd>{pct(current.probability)}</dd><dt>80% predictive count interval</dt><dd>{current.lower}–{current.upper} reports</dd><dt>Direct history</dt><dd>{current.support} mature reports · {current.recent} in the latest training year</dd><dt>Evidence support</dt><dd>{current.evidence}</dd></dl><p className="fine">These are area report estimates, not a traveler’s probability of harm. Nearby areas inform sparse cells; a rounded 0–0 interval can still have a small nonzero probability.</p></div> : <div className="campus-prompt"><MapPin size={24}/><h2>Explore the outlook</h2><p>Select a street or area to inspect its estimates and supporting history.</p></div>}
          <h3>Higher modeled activity</h3><div className="area-list">{[...cells].sort((a, b) => b.properties.density - a.properties.density).slice(0, 5).map(c => <button key={c.properties.cell} onClick={() => {patch({selected: c.properties.cell}); go(bundle.cells[c.properties.cell].center);}}><span>{areaName(c.properties.cell)}<small>{c.properties.evidence} history · {c.properties.density.toFixed(2)} reports/km²/3h</small></span><ArrowRight size={13}/></button>)}</div>
        </>}
        {s.mode === 'history' && report && <article className="history-detail"><button className="text-button" onClick={() => patch({selectedReport: null})}>Close report <X size={13}/></button><span className="eyebrow">Public LAPD record</span><h2>{report[4]}</h2><p>{report[1]} · {report[5] || 'Time unavailable'}</p><dl><dt>Case number</dt><dd>{report[7]}</dd><dt>Reported date</dt><dd>{report[6] || 'Unavailable'}</dd><dt>Classifications</dt><dd>{JSON.parse(report[8]).join(' · ')}</dd></dl>{JSON.parse(report[9]).map((source: Data) => <a key={source.dataset} href={source.url} target="_blank" rel="noreferrer">LAPD source <ArrowUpRight size={12}/></a>)}<p className="fine">A report is not a finding of guilt or a measure of personal risk. Several reports can share this approximate location.</p></article>}
      </aside>

      <div className="campus-map-column"><CampusMap bundle={bundle} places={places} threeD={s.threeD} basemap={s.basemap} surveys={s.surveys} cells={s.mode === 'history' ? undefined : cells} streets={s.mode === 'history' ? undefined : model.streets} selected={s.selected} posts={s.mode === 'posts' ? posts : undefined} covered={covered} route={s.mode === 'routes' ? route.result : undefined} endpoints={s.mode === 'routes' ? endpoints : undefined} history={s.mode === 'history' ? history : undefined} focus={focus} unit={replayUnit} picking={!!picking} onPick={pick} onSelect={selected => patch({selected, mode: 'explore'})} onReport={id => {patch({selectedReport: id}); const row = history.find((r: any[]) => r[0] === id); if (row) go([row[2], row[3]], 16);}} onPost={choosePost}/>
        {picking && <div className="map-pick-notice" role="status">Select {picking === 'start' ? 'your start' : 'your destination'} on a supported walking path.<button onClick={() => setPicking(null)}>Cancel</button></div>}
        <div className="map-caption"><span><i className="legend-line"/>{s.mode === 'history' ? 'Approximate reported locations' : 'Modeled report activity · warmer streets = higher estimates'}</span><button className="text-button" aria-pressed={s.surveys} onClick={() => patch({surveys: !s.surveys})}><Footprints size={13}/>Survey sites {s.surveys ? 'on' : 'off'}</button></div>
        <p className="fine">{s.basemap === 'imagery' ? 'Esri satellite/aerial mosaic: collection dates vary; imagery does not change with departure time. ' : 'OSM map context; named-building coverage is concentrated on central campus. '}Cartography loads online. Analysis and route comparisons use the saved snapshot.</p>
        {s.mode === 'history' && <section className="panel history-list"><div className="section-heading"><h2>Public reports</h2><small>{Math.min(history.length, historyPage * 30)} of {history.length.toLocaleString()}</small></div>{history.slice(0, historyPage * 30).map((r: any[]) => <button key={r[0]} onClick={() => {patch({selectedReport: r[0]}); go([r[2], r[3]], 16);}}><span>{r[4]}<small>{r[1]} · {r[5] || 'Time unspecified'}</small></span><ArrowUpRight size={14}/></button>)}{history.length > historyPage * 30 && <button onClick={() => setHistoryPage(n => n + 1)}>Show 30 more reports</button>}{!history.length && <p>No reports match this date range and category.</p>}</section>}
      </div>
    </div>
    <div className="campus-evidence-footer"><Shield size={15}/><p>Reports through {bundle.data_through} · mature training through {bundle.model.training_end} · {bundle.cells.length} clipped areas. Planning estimates describe reported activity, not personal safety.</p></div>
    <details className="panel campus-method"><summary>Sources, pedestrian evidence & model limitations</summary><div className="method-grid"><section><h3>Precomputed seasonal analysis</h3><p>{bundle.evaluation.selected}. Training: {bundle.model.training_start}–{bundle.model.training_end}. Recency half-life: {bundle.model.half_life_days} days; the latest training year receives more weight than earlier years. Zero-report days, the 30-day maturity buffer, and report availability are included.</p><p>Spatially pooled weighted Poisson rates with approximate Gamma-Poisson count distributions. Dates sharing weekday and three-hour window share an outlook. Person-directed records can include indoor or relationship contexts.</p></section><section><h3>Retrospective validation</h3>{validation?.final_test_folds?.map((f: Data) => <p key={f.cutoff}>{f.cutoff}: {f.report_family_occurrences} report-family occurrences · {pct(f.top_20_percent_area_capture)} captured in the top 20% study area.</p>)}<p>The seasonal model slightly improved count scores, while static density captured more reports in the final hotspot tests. A revised snapshot cannot reconstruct historical publication availability. Route effectiveness and nominal interval calibration are not established.</p><p>{bundle.graph.coverage.cells_with_connections}/{bundle.cells.length} areas have mapped walking connections. Routing uses the largest component; unsupported connections are never invented.</p></section><section><h3>Pedestrian survey evidence</h3>{bundle.pedestrian.observations.map((o: Data) => <div className="survey-item" key={o.name}><strong>{o.name}</strong><p>{o.pedestrians_sampled.toLocaleString()} sampled pedestrians · {o.year}<br/>{o.weekday_period_total} weekday / {o.weekend_period_total} weekend survey totals</p><button onClick={() => {patch({surveys: true}); go(o.coordinates, 16);}}>Locate corridor</button></div>)}<p>These are sampled-period totals, not daily or hourly flow. Corridor markers are approximate; pedestrian flow is excluded from route and staffing scores.</p><a href="https://data.lacity.org/d/hiwe-xzrt" target="_blank" rel="noreferrer">Inspect LADOT source <ArrowUpRight size={12}/></a></section></div><p className="fine">Snapshot {bundle.model_id} · OSM © OpenStreetMap contributors, ODbL. Changing time, map perspective or route endpoints does not fit a new model or request external routing.</p></details>
  </div>;
}
