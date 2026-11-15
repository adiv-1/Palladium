import {useEffect, useState} from 'react';
import {QRCodeSVG} from 'qrcode.react';
import {buildingFacts, CATEGORY_LABEL} from './buildings';
import {DEMO_SPEED, POLICY, progress, remainingSeconds, type Proposal} from './dispatch';
import {officerLink} from './bus';
import {Tag} from './Panels';
import {severityColor} from './layers';
import {INCIDENT_LABEL, SEVERITY_LABEL, UNIT_STATUS_LABEL} from './types';
import type {Building, DispatchEvent, DispatchRecord, Incident, Officer} from './types';

const rgb = (c: ArrayLike<number>) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
const clock = (ms: number) => new Date(ms).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', second: '2-digit'});
const secs = (s: number) => (s < 60 ? `${Math.ceil(s)} s` : `${Math.floor(s / 60)} min ${Math.ceil(s % 60)} s`);
const MODE_ICON: Record<Officer['mode'], string> = {foot: '🚶', bike: '🚲', cart: '🛺'};

function useNow(every = 250) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(id);
  }, [every]);
  return now;
}

const EVENT_TEXT: Record<DispatchEvent['kind'], string> = {
  created: 'Dispatch created', notified: 'Notified', acknowledged: 'Acknowledged', en_route: 'En route',
  on_scene: 'On scene', cleared: 'Cleared', cancelled: 'Dispatch cancelled', support: 'Support',
};

function BuildingFacts({building}: {building: Building}) {
  const facts = buildingFacts(building);
  return (
    <dl className="facts">
      {facts.map((f) => (
        <div key={f.label}><dt>{f.label}</dt><dd>{f.href ? <a href={f.href} target="_blank" rel="noreferrer">{f.value}</a> : f.value}</dd></div>
      ))}
    </dl>
  );
}

export function IncidentDetail(p: {
  incident: Incident | null; building: Building | null; officers: Officer[]; proposals: Proposal[];
  chosen: string[]; onToggleUnit: (id: string) => void; dispatch: DispatchRecord | null;
  onDispatch: () => void; onDismiss: () => void; onResolve: () => void; onCancel: () => void; onClose: () => void;
  devices: Record<string, number>;
}) {
  const now = useNow();
  const i = p.incident;
  if (!i) return null;
  const policy = POLICY[i.type];
  const callsign = (id?: string) => p.officers.find((o) => o.id === id)?.callsign ?? id ?? '';
  const units = p.dispatch ? p.officers.filter((o) => p.dispatch!.unitIds.includes(o.id)) : [];
  return (
    <aside className="panel detail" key={i.id}>
      <div className="panel-head">
        <div>
          <h2>{INCIDENT_LABEL[i.type]}</h2>
          <span className="sev" style={{background: rgb(severityColor(i.severity))}}>{SEVERITY_LABEL[i.severity]}</span>
          <span className={`prio ${policy.priority}`}>{policy.priority}</span>
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

      {p.building && (<>
        <h3>Building <Tag kind="real" title="OpenStreetMap" /></h3>
        <p className="building-name">{p.building.name}</p>
        <BuildingFacts building={p.building} />
      </>)}

      {i.status === 'unreviewed' && (<>
        <h3>Dispatch plan</h3>
        <p className="reason">{policy.reason}</p>
        {policy.support.map((s) => <p className="support" key={s}>{s}</p>)}
        <div className="unit-picker">
          {p.proposals.length === 0 && <p className="fine">No units available.</p>}
          {p.proposals.slice(0, 6).map(({officer, eta}) => (
            <label key={officer.id} className={`unit-option ${p.chosen.includes(officer.id) ? 'on' : ''}`}>
              <input type="checkbox" checked={p.chosen.includes(officer.id)} onChange={() => p.onToggleUnit(officer.id)} />
              <span className="mode">{MODE_ICON[officer.mode]}</span>
              <strong>{officer.callsign}</strong>
              <span className="eta">{secs(eta)}</span>
              {p.devices[officer.id] && now - p.devices[officer.id] < 7000 ? <span className="paired">📱</span> : null}
            </label>
          ))}
        </div>
        <div className="actions">
          <button type="button" className="primary approve" onClick={p.onDispatch} disabled={!p.chosen.length}>
            Dispatch {p.chosen.length} unit{p.chosen.length === 1 ? '' : 's'}
          </button>
          <button type="button" className="ghost" onClick={p.onDismiss}>Dismiss</button>
        </div>
        <p className="fine">ETAs are real-time estimates on straight-line paths. A person decides; nothing is sent automatically.</p>
      </>)}

      {p.dispatch && (<>
        <h3>Units <Tag kind="simulated" /></h3>
        <div className="unit-progress">
          {units.map((u) => {
            const pct = u.status === 'on_scene' ? 1 : u.route ? progress(u.route, now) : 0;
            return (
              <div key={u.id} className={`unit-row ${u.status}`}>
                <div className="unit-line">
                  <span className="mode">{MODE_ICON[u.mode]}</span>
                  <strong>{u.callsign}</strong>
                  <span className={`unit-status ${u.status}`}>{UNIT_STATUS_LABEL[u.status]}</span>
                  {u.status === 'en_route' && u.route && <span className="eta">{secs(remainingSeconds(u.route, now))}</span>}
                </div>
                <div className="bar"><span style={{width: `${pct * 100}%`}} /></div>
              </div>
            );
          })}
          {p.dispatch.status !== 'active' && <p className={`decided ${p.dispatch.status}`}>{p.dispatch.status === 'resolved' ? 'Resolved' : 'Cancelled'}</p>}
        </div>
        <p className="fine">Travel runs at {DEMO_SPEED}× speed for the demo.</p>
        <h3>Timeline</h3>
        <ol className="timeline">
          {p.dispatch.events.map((e, n) => (
            <li key={n} className={e.kind}>
              <time>{clock(e.at)}</time>
              <span>{EVENT_TEXT[e.kind]}{e.unitId ? ` · ${callsign(e.unitId)}` : ''}{e.note ? ` · ${e.note}` : ''}</span>
            </li>
          ))}
        </ol>
        {p.dispatch.status === 'active' && (
          <div className="actions">
            <button type="button" className="primary" onClick={p.onResolve}>Resolve incident</button>
            <button type="button" className="ghost" onClick={p.onCancel}>Cancel dispatch</button>
          </div>
        )}
      </>)}
      {i.status === 'dismissed' && <p className="decided dismissed">Dismissed{i.decidedAt ? ` at ${clock(i.decidedAt)}` : ''}</p>}
    </aside>
  );
}

export function BuildingCard({building, onClose, racksNearby, camerasNearby}: {building: Building | null; onClose: () => void; racksNearby: number; camerasNearby: number}) {
  if (!building) return null;
  return (
    <aside className="panel detail building-card" key={building.id}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">{CATEGORY_LABEL[building.category]}</span>
          <h2>{building.name}</h2>
          <Tag kind="real" title="OpenStreetMap" />
        </div>
        <button type="button" className="ghost close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <BuildingFacts building={building} />
      <div className="nearby">
        <div><strong>{racksNearby}</strong><span>bike racks within 60 m <Tag kind="real" /></span></div>
        <div><strong>{camerasNearby}</strong><span>cameras nearby <Tag kind="simulated" /></span></div>
      </div>
      <p className="fine">Building data © OpenStreetMap contributors. Missing details are missing in OSM; nothing is filled in.</p>
    </aside>
  );
}

export function UnitsPanel({officers, devices, room, relay, onFocus}: {officers: Officer[]; devices: Record<string, number>; room: string; relay: boolean; onFocus: (o: Officer) => void}) {
  const now = useNow(1000);
  const [pairing, setPairing] = useState<Officer | null>(null);
  const [open, setOpen] = useState(true);
  return (
    <>
      <div className={`panel units ${open ? '' : 'collapsed'}`}>
        <button type="button" className="units-head" onClick={() => setOpen(!open)}>
          <strong>Units</strong>
          <span>{officers.filter((o) => o.status === 'available').length} available</span>
          <Tag kind="simulated" />
        </button>
        {open && officers.map((o) => {
          const paired = devices[o.id] && now - devices[o.id] < 7000;
          return (
            <div key={o.id} className={`unit-item ${o.status}`}>
              <button type="button" className="unit-main" onClick={() => onFocus(o)}>
                <span className="mode">{MODE_ICON[o.mode]}</span>
                <strong>{o.callsign}</strong>
                <span className={`unit-status ${o.status}`}>{UNIT_STATUS_LABEL[o.status]}</span>
              </button>
              <button type="button" className={`pair ${paired ? 'on' : ''}`} onClick={() => setPairing(o)} title="Open this unit on a phone">
                {paired ? '📱 linked' : 'Pair phone'}
              </button>
            </div>
          );
        })}
      </div>
      {pairing && (
        <div className="modal-backdrop" onClick={() => setPairing(null)}>
          <div className="panel modal pair" onClick={(e) => e.stopPropagation()}>
            <h2>Open {pairing.callsign} on a phone</h2>
            <div className="qr"><QRCodeSVG value={officerLink(room, pairing.id)} size={190} bgColor="#ffffff" fgColor="#111318" /></div>
            <a className="pair-link" href={officerLink(room, pairing.id)} target="_blank" rel="noreferrer">{officerLink(room, pairing.id)}</a>
            {location.hostname === 'localhost' || location.hostname === '127.0.0.1'
              ? <p className="fine warn">This link uses localhost, so it only works on this computer (open it in another tab). For a phone, open the dashboard through your network address, which Vite prints as "Network:".</p>
              : null}
            <p className="fine">{relay ? 'Relay connected: phones on the same network receive dispatches.' : 'SafeSC relay not reachable: only tabs on this computer receive dispatches. Start the SafeSC API for phones.'}</p>
            <div className="actions"><button type="button" className="ghost" onClick={() => setPairing(null)}>Done</button></div>
          </div>
        </div>
      )}
    </>
  );
}
