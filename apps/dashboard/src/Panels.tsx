import {useEffect, useState} from 'react';
import type {FormEvent} from 'react';
import {severityColor} from './layers';
import type {LayerToggles} from './layers';
import {INCIDENT_LABEL, SEVERITY_LABEL} from './types';
import type {Funnel, Incident, Officer, Provenance} from './types';

const rgb = (c: ArrayLike<number>) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;

const TAG_TEXT: Record<Provenance, string> = {real: 'Real', demo: 'Demo', simulated: 'Simulated', estimate: 'Estimate', live: 'Live'};

export function Tag({kind, title}: {kind: Provenance; title?: string}) {
  return <span className={`tag tag-${kind}`} title={title}>{TAG_TEXT[kind]}</span>;
}

function ago(ms: number, now: number) {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function useNow(every = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(id);
  }, [every]);
  return now;
}

function eta(seconds: number) {
  return seconds < 60 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
}

function hourLabel(h: number) {
  const suffix = h < 12 ? 'AM' : 'PM';
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve} ${suffix}`;
}

export function StatusStrip(p: {
  camerasTriaged: number; watchedLive: number; vlmCalls: number; costToday: number; costProvenance: 'estimate';
  mapMode: 'google' | 'maplibre'; googleAvailable: boolean; onToggleMap: () => void; liveStatus: string | null;
}) {
  const chips: [string, string, Provenance, string?][] = [
    ['AI-triaged cameras', p.camerasTriaged.toLocaleString(), 'simulated'],
    ['Watched live', p.watchedLive.toLocaleString(), 'simulated'],
    ['VLM checks today', p.vlmCalls.toLocaleString(), p.liveStatus === 'connected' ? 'live' : 'demo'],
    ['Est. cost today', `$${p.costToday.toFixed(2)}`, p.costProvenance, 'L4 GPU at $0.60/h, about 40 cameras per GPU'],
  ];
  return (
    <header className="strip">
      <div className="brand"><strong>SafeSC</strong><span>Command view</span></div>
      <div className="chips">
        {chips.map(([label, value, kind, title]) => (
          <div className="chip" key={label} title={title}>
            <span className="chip-label">{label}</span>
            <strong>{value}</strong>
            <Tag kind={kind} />
          </div>
        ))}
      </div>
      <div className="strip-right">
        {p.liveStatus && <span className={`live-status ${p.liveStatus}`}>SafeSC {p.liveStatus}</span>}
        <button type="button" className="ghost" onClick={p.onToggleMap} disabled={!p.googleAvailable && p.mapMode === 'maplibre'}
          title={p.googleAvailable ? 'Switch basemap (M)' : 'Set VITE_GOOGLE_MAPS_API_KEY to enable photoreal 3D tiles'}>
          {p.mapMode === 'google' ? 'Photoreal 3D' : 'Fallback map'}{p.googleAvailable ? ' · M' : ''}
        </button>
      </div>
    </header>
  );
}

export function IncidentQueue(p: {incidents: Incident[]; selectedId: string | null; onSelect: (id: string) => void; onDemo: () => void; onConnect: () => void}) {
  const now = useNow();
  const open = p.incidents.filter((i) => i.status === 'unreviewed').length;
  return (
    <aside className="panel queue">
      <div className="panel-head">
        <h2>Incident queue</h2>
        <span className="count">{open} open · {p.incidents.length} total</span>
      </div>
      <div className="queue-actions">
        <button type="button" className="primary" onClick={p.onDemo}>Stage demo incident</button>
        <button type="button" className="ghost" onClick={p.onConnect}>Connect SafeSC</button>
      </div>
      <div className="queue-list">
        {p.incidents.length === 0 && <p className="empty">No incidents yet. Stage a demo incident or connect a running SafeSC.</p>}
        {p.incidents.map((i) => (
          <button type="button" key={i.id} className={`queue-item ${i.status} ${i.id === p.selectedId ? 'selected' : ''}`} onClick={() => p.onSelect(i.id)}>
            <span className="dot" style={{background: rgb(severityColor(i.severity))}} />
            <span className="queue-copy">
              <strong>{INCIDENT_LABEL[i.type]}</strong>
              <small>{i.place}</small>
              <small>{ago(i.createdAt, now)} · {i.status}</small>
            </span>
            <Tag kind={i.origin} />
          </button>
        ))}
      </div>
    </aside>
  );
}

export function IncidentDetail(p: {incident: Incident | null; recommendation: {officer: Officer; eta: number} | null; onApprove: () => void; onDismiss: () => void; onClose: () => void}) {
  const i = p.incident;
  if (!i) return null;
  return (
    <aside className="panel detail">
      <div className="panel-head">
        <div>
          <h2>{INCIDENT_LABEL[i.type]}</h2>
          <span className="sev" style={{background: rgb(severityColor(i.severity))}}>{SEVERITY_LABEL[i.severity]}</span>
          <Tag kind={i.origin} />
        </div>
        <button type="button" className="ghost close" onClick={p.onClose} aria-label="Close">×</button>
      </div>
      <p className="place">{i.place} · {i.cameraId}</p>
      {i.thumbnail ? <img className="clip" src={i.thumbnail} alt="Incident frame" /> : <div className="clip empty">Demo event · no footage</div>}
      <p className="observation">{i.observation}</p>
      {i.evidence.length > 0 && (<>
        <h3>Evidence</h3>
        <ul className="evidence">{i.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
      </>)}
      {i.uncertainties.length > 0 && (<>
        <h3>Uncertainty</h3>
        <ul className="uncertainty">{i.uncertainties.map((u) => <li key={u}>{u}</li>)}</ul>
      </>)}
      <p className="fine">Score {(i.confidence * 100).toFixed(0)}% · uncalibrated, not a probability of harm</p>
      <h3>Recommended responder</h3>
      {p.recommendation ? (
        <div className="responder">
          <strong>{p.recommendation.officer.callsign}</strong>
          <span>{p.recommendation.officer.mode} · ETA ≈ {eta(p.recommendation.eta)}</span>
          <Tag kind="simulated" />
        </div>
      ) : <p className="fine">No officer available.</p>}
      {i.status === 'unreviewed' ? (
        <div className="actions">
          <button type="button" className="primary approve" onClick={p.onApprove} disabled={!p.recommendation}>Approve & dispatch</button>
          <button type="button" className="ghost" onClick={p.onDismiss}>Dismiss</button>
        </div>
      ) : (
        <p className={`decided ${i.status}`}>{i.status === 'approved' ? 'Approved and dispatched' : 'Dismissed'}
          {i.decidedAt ? ` at ${new Date(i.decidedAt).toLocaleTimeString()}` : ''}</p>
      )}
      <p className="fine">A person decides. Nothing is dispatched automatically.</p>
    </aside>
  );
}

export function BottomBar(p: {hour: number | null; setHour: (h: number | null) => void; playing: boolean; setPlaying: (b: boolean) => void; funnel: Funnel; toggles: LayerToggles; setToggles: (t: LayerToggles) => void}) {
  const steps: [string, number][] = [
    ['Frames analyzed', p.funnel.frames], ['Rule candidates', p.funnel.candidates], ['VLM checks', p.funnel.vlmChecks],
    ['Alerts', p.funnel.alerts], ['Approved', p.funnel.approved],
  ];
  const toggle = (key: keyof LayerToggles) => p.setToggles({...p.toggles, [key]: !p.toggles[key]});
  return (
    <footer className="panel bottom">
      <div className="history">
        <div className="history-head">
          <span>History {p.hour === null ? '· all day' : `· ${hourLabel(p.hour)}`}</span>
          <Tag kind="simulated" title="Simulated history; replace with geocoded DPS log" />
        </div>
        <div className="history-controls">
          <button type="button" className="ghost" onClick={() => {
            if (!p.playing && p.hour === null) p.setHour(0);
            p.setPlaying(!p.playing);
          }}>{p.playing ? 'Pause' : 'Play'}</button>
          <input className="scrub" type="range" min={0} max={23} step={1} value={p.hour ?? 0} aria-label="Hour of day"
            onChange={(e) => {p.setPlaying(false); p.setHour(Number(e.target.value));}} />
          <button type="button" className={`ghost ${p.hour === null ? 'active' : ''}`} onClick={() => {p.setPlaying(false); p.setHour(null);}}>All day</button>
        </div>
      </div>
      <div className="funnel">
        {steps.map(([label, value], n) => (
          <div className="step" key={label}>
            <strong>{value.toLocaleString()}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <div className="toggles">
        {(['history', 'coverage', 'racks'] as (keyof LayerToggles)[]).map((key) => (
          <label key={key}><input type="checkbox" checked={p.toggles[key]} onChange={() => toggle(key)} />{key}</label>
        ))}
      </div>
    </footer>
  );
}

export function ConnectDialog(p: {open: boolean; onClose: () => void; onSubmit: (token: string) => Promise<void>}) {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!p.open) return null;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      await p.onSubmit(token.trim());
      p.onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-backdrop" onClick={p.onClose}>
      <form className="panel modal" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h2>Connect to SafeSC</h2>
        <p className="fine">Enter the reviewer access token from data/app_token. Live incidents appear on simulated camera positions.</p>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Access token" autoFocus />
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="submit" className="primary" disabled={busy || !token.trim()}>{busy ? 'Connecting…' : 'Connect'}</button>
          <button type="button" className="ghost" onClick={p.onClose}>Cancel</button>
        </div>
      </form>
    </div>
  );
}

export function Legend() {
  const beams: [string, ArrayLike<number>][] = [
    ['High priority', severityColor('HIGH_PRIORITY_REVIEW')], ['Review', severityColor('REVIEW_REQUIRED')], ['Watch', severityColor('WATCH')],
  ];
  return (
    <div className="panel legend">
      {beams.map(([label, c]) => <div key={label}><span className="swatch beam" style={{background: rgb(c)}} />{label}</div>)}
      <div><span className="swatch cone gold" />Watched live</div>
      <div><span className="swatch cone teal" />AI-triaged</div>
      <div><span className="swatch dot rack" />Bike rack <Tag kind="real" /></div>
      <div><span className="swatch dot officer" />Officer <Tag kind="simulated" /></div>
      <p className="fine">Camera positions and officers are simulated. Never real DPS locations.</p>
    </div>
  );
}
