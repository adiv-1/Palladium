import {useState} from 'react';
import {ALERT_RADIUS_M, type ActiveAlert} from './alerts';
import {INCIDENT_LABEL, type Incident} from './types';
import {Tag} from './Panels';
import {studentLink} from './Nav';

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', second: '2-digit'});

// Shown at the top of the Live page while an AI-flagged incident waits for the admin.
export function PendingBanner({incidents, onReview}: {incidents: Incident[]; onReview: (i: Incident) => void}) {
  const open = incidents.filter((i) => i.status === 'unreviewed' && i.debate !== 'suppress');
  if (!open.length) return null;
  const i = open[0];
  return (
    <div className="pending" role="status">
      <span className="siren" aria-hidden />
      <div><strong>AI flagged: {INCIDENT_LABEL[i.type]}</strong><small>{i.place} · awaiting your approval{open.length > 1 ? ` · ${open.length - 1} more in the queue` : ''}</small></div>
      <button className="primary" onClick={() => onReview(i)}>Review</button>
    </div>
  );
}

export function AlertPanel({alert, devices, onClose}: {alert: ActiveAlert; devices: Record<string, number>; onClose: () => void}) {
  const [copied, setCopied] = useState(false);
  const inside = alert.users.filter((u) => u.inside).length;
  const phones = Object.entries(devices).filter(([id, at]) => id.startsWith('student-') && Date.now() - at < 8000).length;
  const r = alert.report;
  const copy = async () => {
    try {await navigator.clipboard.writeText(JSON.stringify(r, null, 2)); setCopied(true); setTimeout(() => setCopied(false), 1800);} catch { /* clipboard unavailable */ }
  };
  return (
    <section className="panel alert-panel" aria-label="Alert broadcast and incident report">
      <div className="panel-head">
        <div><span className="eyebrow">Alert agent · sent {clock(alert.msg.at)}</span><h2>{alert.msg.title}</h2></div>
        <button className="ghost close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="stat-row">
        <div><b>{alert.officers.length}</b><span>DPS units alerted</span></div>
        <div><b>{inside}</b><span>app users within {ALERT_RADIUS_M} m <Tag kind="simulated" /></span></div>
        <div><b>{phones}</b><span>real student phones linked</span><a className="muted-link" href={studentLink()} target="_blank" rel="noreferrer">Open the student app</a></div>
      </div>
      <blockquote className="sms">{alert.msg.message}</blockquote>
      <div className="report">
        <div className="report-head"><strong>Incident report {r.id}</strong><Tag kind="demo" title="Placeholder values, hard-coded for testing the alert flow" /></div>
        <dl>
          <div><dt>Type</dt><dd>{r.type} ({r.severity})</dd></div>
          <div><dt>Location</dt><dd>{r.location}{r.address ? `, ${r.address}` : ''}</dd></div>
          <div><dt>Camera</dt><dd>{r.camera} (simulated)</dd></div>
          <div><dt>Units</dt><dd>{r.unitsAssigned.join(', ') || 'pending'}</dd></div>
          {r.individuals.map((p) => (
            <div key={p.label} className="person"><dt>{p.label}</dt><dd>about {p.height}, {p.build} build, {p.clothing}; last seen heading {p.heading}</dd></div>
          ))}
          <div><dt>Status</dt><dd>{r.status}</dd></div>
        </dl>
        <p className="fine">Descriptors are placeholders. The vision pipeline does not estimate them, and Palladium never infers identity or demographics.</p>
        <button className="ghost" onClick={copy}>{copied ? 'Copied' : 'Copy report JSON'}</button>
      </div>
    </section>
  );
}
