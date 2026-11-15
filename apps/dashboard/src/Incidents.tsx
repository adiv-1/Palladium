import {useMemo, useState} from 'react';
import {hourLabel, type Past} from './analysis';
import {Nav} from './Nav';
import {Tag} from './Panels';
import {useCampus} from './data';
import {INCIDENT_LABEL, type IncidentType} from './types';
import './incidents.css';

const TYPES: IncidentType[] = ['altercation', 'person_down', 'rack_activity'];
const STACK: IncidentType[] = ['rack_activity', 'person_down', 'altercation', 'other'];
const COLOR: Record<IncidentType, string> = {altercation: 'var(--red)', person_down: 'color-mix(in srgb, var(--yellow) 70%, var(--red))', rack_activity: 'var(--yellow)', other: 'var(--faint)'};
const DAY = 864e5;
const PAGE = 60;
const WEEKS = 13;
const plural = (n: number, w: string) => `${n.toLocaleString()} ${w}${n === 1 ? '' : 's'}`;
const empty = (): Record<IncidentType, number> => ({altercation: 0, person_down: 0, rack_activity: 0, other: 0});
const dominant = (c: Record<IncidentType, number>) => (Object.keys(c) as IncidentType[]).reduce((a, b) => (c[b] > c[a] ? b : a));
const fmtDate = (ts: number) => new Date(ts).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});

function niceMax(v: number) {
  if (v <= 4) return 4;
  const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function Pill({type}: {type: IncidentType}) {
  return <span className="inc-pill" style={{['--c' as string]: COLOR[type]}}><i />{INCIDENT_LABEL[type]}</span>;
}

type Stats = {
  total: number; last7: number; prev7: number; hours: Record<IncidentType, number>[]; hourTotals: number[]; peak: number;
  weeks: Record<IncidentType, number>[]; weekStart: number[];
  places: {place: string; n: number; top: IncidentType; hours: number[]; peak: number}[];
};

function analyse(past: Past[]): Stats {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const m = midnight.getTime();
  const hours = Array.from({length: 24}, empty);
  const weeks = Array.from({length: WEEKS}, empty); // index 0 = oldest
  const byPlace = new Map<string, {n: number; t: Record<IncidentType, number>; h: number[]}>();
  let last7 = 0, prev7 = 0;
  for (const p of past) {
    hours[p.hour][p.type]++;
    const dayIdx = Math.max(1, Math.ceil((m - p.ts) / DAY)); // 1 = yesterday
    if (dayIdx <= 7) last7++; else if (dayIdx <= 14) prev7++;
    const w = Math.floor((dayIdx - 1) / 7);
    if (w < WEEKS) weeks[WEEKS - 1 - w][p.type]++;
    const e = byPlace.get(p.place) ?? {n: 0, t: empty(), h: new Array(24).fill(0)};
    e.n++; e.t[p.type]++; e.h[p.hour]++;
    byPlace.set(p.place, e);
  }
  const hourTotals = hours.map((h) => h.altercation + h.person_down + h.rack_activity + h.other);
  const places = [...byPlace.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, 8)
    .map(([place, e]) => ({place, n: e.n, top: dominant(e.t), hours: e.h, peak: e.h.indexOf(Math.max(...e.h))}));
  return {
    total: past.length, last7, prev7, hours, hourTotals, peak: hourTotals.indexOf(Math.max(...hourTotals)),
    weeks, weekStart: weeks.map((_, i) => m - (WEEKS - i) * 7 * DAY + DAY), places,
  };
}

// ---------- hour-of-day stacked bars ----------
function HourChart({s}: {s: Stats}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 720, H = 250, L = 38, R = 8, T = 10, B = 28;
  const max = niceMax(Math.max(...s.hourTotals));
  const bw = (W - L - R) / 24, y = (v: number) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, 1, 2, 3, 4].map((i) => (max / 4) * i);
  const hv = hover == null ? null : s.hours[hover];
  return (
    <div className="inc-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Incidents by hour of day. Peak at ${hourLabel(s.peak)} with ${s.hourTotals[s.peak]} incidents.`}>
        {ticks.map((t) => (
          <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" /><text x={L - 6} y={y(t) + 3} textAnchor="end" className="axis">{Math.round(t)}</text></g>
        ))}
        {s.hours.map((h, i) => {
          let acc = 0;
          return (
            <g key={i} className={`bar${hover === i ? ' hot' : ''}${i === s.peak ? ' peak' : ''}`}>
              {STACK.map((t) => {
                const v = h[t];
                if (!v) return null;
                const y0 = y(acc), y1 = y(acc + v);
                acc += v;
                return <rect key={t} x={L + i * bw + 2} width={bw - 4} y={y1} height={Math.max(0, y0 - y1)} fill={COLOR[t]} rx={1.5} />;
              })}
            </g>
          );
        })}
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => <text key={h} x={L + h * bw + bw / 2} y={H - 9} textAnchor="middle" className="axis">{hourLabel(h)}</text>)}
        {s.hours.map((_, i) => (
          <rect key={i} x={L + i * bw} y={T} width={bw} height={H - T - B} className="hit" tabIndex={0}
            aria-label={`${hourLabel(i)}: ${plural(s.hourTotals[i], 'incident')}`}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
        ))}
      </svg>
      {hover != null && hv && (
        <div className="inc-tip" style={{left: `${((L + hover * bw + bw / 2) / W) * 100}%`}} role="status">
          <b>{hourLabel(hover)}</b>
          {TYPES.map((t) => <span key={t}><i style={{background: COLOR[t]}} />{INCIDENT_LABEL[t]}<em>{hv[t]}</em></span>)}
          <span className="sum">Total<em>{s.hourTotals[hover]}</em></span>
        </div>
      )}
    </div>
  );
}

// ---------- 13-week trend ----------
function TrendChart({s}: {s: Stats}) {
  const [on, setOn] = useState<Record<string, boolean>>({altercation: true, person_down: true, rack_activity: true});
  const [hover, setHover] = useState<number | null>(null);
  const active = TYPES.filter((t) => on[t]);
  const W = 720, H = 250, L = 38, R = 14, T = 12, B = 28;
  const max = niceMax(Math.max(1, ...s.weeks.map((w) => Math.max(...active.map((t) => w[t])))));
  const step = (W - L - R) / (WEEKS - 1);
  const x = (i: number) => L + i * step, y = (v: number) => T + (H - T - B) * (1 - v / max);
  const ticks = [0, 1, 2, 3, 4].map((i) => (max / 4) * i);
  return (
    <>
      <div className="inc-chips" role="group" aria-label="Series">
        {TYPES.map((t) => (
          <button key={t} type="button" className={`inc-chip${on[t] ? ' on' : ''}`} aria-pressed={on[t]} style={{['--c' as string]: COLOR[t]}}
            onClick={() => setOn((o) => ({...o, [t]: !o[t]}))}><i />{INCIDENT_LABEL[t]}</button>
        ))}
      </div>
      <div className="inc-chart">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Weekly incident counts over the last 13 weeks, by type.">
          <defs>
            {TYPES.map((t) => (
              <linearGradient key={t} id={`inc-g-${t}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={COLOR[t]} stopOpacity="0.16" /><stop offset="1" stopColor={COLOR[t]} stopOpacity="0" />
              </linearGradient>
            ))}
          </defs>
          {ticks.map((t) => (
            <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" /><text x={L - 6} y={y(t) + 3} textAnchor="end" className="axis">{Math.round(t)}</text></g>
          ))}
          {s.weekStart.map((ts, i) => (i % 2 === 0 || i === WEEKS - 1) && i !== WEEKS - 2 ? (
            <text key={i} x={x(i)} y={H - 9} textAnchor="middle" className="axis">{fmtDate(ts)}</text>
          ) : null)}
          {active.map((t) => {
            const pts = s.weeks.map((w, i) => `${x(i)},${y(w[t])}`);
            return (
              <g key={t} className="series">
                <polygon points={`${x(0)},${y(0)} ${pts.join(' ')} ${x(WEEKS - 1)},${y(0)}`} fill={`url(#inc-g-${t})`} />
                <polyline points={pts.join(' ')} fill="none" stroke={COLOR[t]} strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />
              </g>
            );
          })}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="guide" />}
          {hover != null && active.map((t) => <circle key={t} cx={x(hover)} cy={y(s.weeks[hover][t])} r={4} fill={COLOR[t]} stroke="var(--surface)" strokeWidth={2} />)}
          {s.weeks.map((w, i) => (
            <rect key={i} x={x(i) - step / 2} y={T} width={step} height={H - T - B} className="hit" tabIndex={0}
              aria-label={`Week of ${fmtDate(s.weekStart[i])}: ${TYPES.map((t) => `${w[t]} ${INCIDENT_LABEL[t]}`).join(', ')}`}
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
          ))}
        </svg>
        {hover != null && (
          <div className="inc-tip" style={{left: `${(x(hover) / W) * 100}%`}} role="status">
            <b>Week of {fmtDate(s.weekStart[hover])}</b>
            {TYPES.map((t) => <span key={t} className={on[t] ? '' : 'dim'}><i style={{background: COLOR[t]}} />{INCIDENT_LABEL[t]}<em>{s.weeks[hover][t]}</em></span>)}
          </div>
        )}
      </div>
    </>
  );
}

function Spark({hours, peak, color}: {hours: number[]; peak: number; color: string}) {
  const w = 96, h = 26, max = Math.max(1, ...hours);
  const pts = hours.map((v, i) => [(i / 23) * (w - 4) + 2, h - 3 - (v / max) * (h - 8)]);
  return (
    <svg className="inc-spark" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Hourly profile, peaks at ${hourLabel(peak)}`}>
      <polyline points={pts.map((p) => p.join(',')).join(' ')} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" />
      <circle cx={pts[peak][0]} cy={pts[peak][1]} r={2.6} fill={color} />
    </svg>
  );
}

function Delta({now, before}: {now: number; before: number}) {
  const d = now - before;
  const pct = before ? Math.round((Math.abs(d) / before) * 100) : null;
  const dir = d > 0 ? 'up' : d < 0 ? 'down' : 'flat';
  return (
    <span className={`inc-delta ${dir}`} title="Compared with the 7 days before">
      <span aria-hidden>{dir === 'up' ? '▲' : dir === 'down' ? '▼' : '■'}</span>
      {dir === 'flat' ? 'No change' : `${Math.abs(d)} ${dir === 'up' ? 'more' : 'fewer'}${pct != null ? ` (${pct}%)` : ''}`}
      <small> vs prior 7 days ({before})</small>
    </span>
  );
}

function Card({i, title, sub, children, className = ''}: {i: number; title: string; sub?: string; children: React.ReactNode; className?: string}) {
  return (
    <section className={`inc-card ${className}`} style={{['--i' as string]: i}} aria-label={title}>
      <header><h2>{title}</h2>{sub && <p>{sub}</p>}</header>
      {children}
    </section>
  );
}

export default function Incidents() {
  const campus = useCampus();
  const past = campus?.past;
  const s = useMemo(() => (past ? analyse(past) : null), [past]);
  const sorted = useMemo(() => (past ? [...past].sort((a, b) => b.ts - a.ts) : []), [past]);
  const [types, setTypes] = useState<Record<string, boolean>>({});
  const [q, setQ] = useState('');
  const [shown, setShown] = useState(PAGE);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const any = TYPES.some((t) => types[t]);
    return sorted.filter((p) => (!any || types[p.type]) && (!needle || p.place.toLowerCase().includes(needle) || INCIDENT_LABEL[p.type].toLowerCase().includes(needle)));
  }, [sorted, types, q]);

  const simulated = campus ? !campus.historyReal : true;

  return (
    <>
      <header className="strip">
        <Nav active="incidents" />
        <div className="chips">
          <div className="chip"><span className="chip-label">Window</span><strong>Last 90 days</strong></div>
          <div className="chip"><span className="chip-label">Incidents</span><strong>{s ? s.total.toLocaleString() : '...'}</strong><Tag kind={simulated ? 'simulated' : 'real'} /></div>
        </div>
      </header>
      <main className="inc-page">
        <div className="inc-wrap">
          <div className="inc-hero" style={{['--i' as string]: 0}}>
            <div>
              <h1>Past incidents</h1>
              <p>Everything logged over the last 90 days: when it happens, where it clusters and which way it is trending.</p>
            </div>
            <div className="inc-note">
              <Tag kind={simulated ? 'simulated' : 'real'} />
              <span>{simulated
                ? 'History is simulated until a geocoded DPS log exists at public/osm/history.json.'
                : 'Loaded from the geocoded DPS log at public/osm/history.json.'}</span>
            </div>
          </div>

          {!s ? <p className="inc-loading" role="status">Loading incident history...</p> : (
            <>
              <div className="inc-kpis">
                <div className="inc-kpi" style={{['--i' as string]: 1}}>
                  <span>Total incidents</span><strong>{s.total.toLocaleString()}</strong><small>across 90 days, about {(s.total / 90).toFixed(1)} a day</small>
                </div>
                <div className="inc-kpi" style={{['--i' as string]: 2}}>
                  <span>Last 7 days</span><strong>{s.last7}</strong><Delta now={s.last7} before={s.prev7} />
                </div>
                <div className="inc-kpi" style={{['--i' as string]: 3}}>
                  <span>Most affected location</span><strong className="txt">{s.places[0]?.place ?? 'None'}</strong>
                  <small>{s.places[0] ? `${plural(s.places[0].n, 'incident')}, ${Math.round((s.places[0].n / s.total) * 100)}% of all` : ''}</small>
                </div>
                <div className="inc-kpi" style={{['--i' as string]: 4}}>
                  <span>Peak hour</span><strong>{hourLabel(s.peak)}</strong><small>{plural(s.hourTotals[s.peak], 'incident')}, {Math.round((s.hourTotals[s.peak] / s.total) * 100)}% of all</small>
                </div>
              </div>

              <div className="inc-grid">
                <Card i={5} title="By hour of day" sub="Incidents per hour, stacked by type">
                  <HourChart s={s} />
                  <div className="inc-legend">{TYPES.map((t) => <span key={t}><i style={{background: COLOR[t]}} />{INCIDENT_LABEL[t]}</span>)}</div>
                </Card>
                <Card i={6} title="13-week trend" sub="Weekly counts, oldest to newest">
                  <TrendChart s={s} />
                </Card>
              </div>

              <Card i={7} title="Top locations" sub="Eight places with the most incidents">
                <div className="inc-scroll">
                  <table className="inc-table">
                    <thead><tr><th scope="col">Location</th><th scope="col" className="num">Incidents</th><th scope="col">Share</th><th scope="col">Dominant type</th><th scope="col">By hour</th></tr></thead>
                    <tbody>
                      {s.places.map((p) => (
                        <tr key={p.place} tabIndex={0}>
                          <th scope="row">{p.place}</th>
                          <td className="num">{p.n}</td>
                          <td className="share"><span style={{width: `${(p.n / s.places[0].n) * 100}%`, background: COLOR[p.top]}} /></td>
                          <td><Pill type={p.top} /></td>
                          <td><Spark hours={p.hours} peak={p.peak} color={COLOR[p.top]} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card i={8} title="Incident log" sub="Newest first">
                <div className="inc-filters">
                  <div className="inc-chips" role="group" aria-label="Filter by type">
                    {TYPES.map((t) => (
                      <button key={t} type="button" className={`inc-chip${types[t] ? ' on' : ''}`} aria-pressed={!!types[t]} style={{['--c' as string]: COLOR[t]}}
                        onClick={() => {setTypes((o) => ({...o, [t]: !o[t]})); setShown(PAGE);}}><i />{INCIDENT_LABEL[t]}</button>
                    ))}
                  </div>
                  <label className="inc-search">
                    <span className="sr">Search location</span>
                    <input type="search" placeholder="Search location or type" value={q} onChange={(e) => {setQ(e.target.value); setShown(PAGE);}} />
                  </label>
                  <span className="inc-count" role="status" aria-live="polite">{plural(filtered.length, 'match')}</span>
                </div>
                <div className="inc-scroll">
                  <table className="inc-table log">
                    <thead><tr><th scope="col">Date</th><th scope="col">Hour</th><th scope="col">Type</th><th scope="col">Location</th></tr></thead>
                    <tbody>
                      {filtered.slice(0, shown).map((p) => (
                        <tr key={p.id} tabIndex={0}>
                          <td>{new Date(p.ts).toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'})}</td>
                          <td>{hourLabel(p.hour)}</td>
                          <td><Pill type={p.type} /></td>
                          <td>{p.place}</td>
                        </tr>
                      ))}
                      {!filtered.length && <tr><td colSpan={4} className="inc-none">No incidents match these filters.</td></tr>}
                    </tbody>
                  </table>
                </div>
                {shown < filtered.length && (
                  <button type="button" className="inc-more" onClick={() => setShown((n) => n + PAGE)}>
                    Load more <small>({Math.min(PAGE, filtered.length - shown)} of {filtered.length - shown} remaining)</small>
                  </button>
                )}
              </Card>
            </>
          )}
        </div>
      </main>
    </>
  );
}
