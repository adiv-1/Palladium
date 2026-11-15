import {useEffect, useRef, useState} from 'react';
import {openBus, type Assignment, type Bus, type BusMessage} from './bus';
import {directionsUrl} from './buildings';
import {UNIT_STATUS_LABEL, type UnitStatus} from './types';

// The page an officer opens on their phone from the pairing link: ?unit=officer-3&room=...
export default function OfficerView({unitId, room}: {unitId: string; room: string}) {
  const [callsign, setCallsign] = useState(unitId);
  const [status, setStatus] = useState<UnitStatus>('available');
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [eta, setEta] = useState<number | undefined>();
  const [linked, setLinked] = useState(false);
  const [alertsOn, setAlertsOn] = useState(false);
  const bus = useRef<Bus | null>(null);
  const audio = useRef<AudioContext | null>(null);
  const lastDispatch = useRef<string | null>(null);

  const chime = () => {
    navigator.vibrate?.([220, 120, 220, 120, 420]);
    const ctx = audio.current;
    if (!ctx) return;
    [880, 1175, 1568].forEach((f, n) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.frequency.value = f; osc.type = 'sine';
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + n * 0.16);
      gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + n * 0.16 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + n * 0.16 + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + n * 0.16); osc.stop(ctx.currentTime + n * 0.16 + 0.32);
    });
  };

  useEffect(() => {
    const onMessage = (m: BusMessage) => {
      if (m.type !== 'state' || m.unitId !== unitId) return;
      setLinked(true);
      setCallsign(m.callsign);
      setStatus(m.status);
      setEta(m.etaSeconds);
      setAssignment(m.assignment);
      if (m.assignment && m.assignment.dispatchId !== lastDispatch.current && m.status === 'assigned') chime();
      lastDispatch.current = m.assignment?.dispatchId ?? null;
    };
    bus.current = openBus(room, onMessage);
    bus.current.send({type: 'hello', unitId});
    const presence = setInterval(() => bus.current?.send({type: 'presence', unitId}), 2000);
    bus.current.send({type: 'presence', unitId});
    return () => {clearInterval(presence); bus.current?.close();};
  }, [unitId, room]);

  const send = (type: 'ack' | 'arrived' | 'clear') => {
    if (assignment) bus.current?.send({type, unitId, dispatchId: assignment.dispatchId});
  };

  const enableAlerts = () => {
    try {audio.current = new AudioContext(); setAlertsOn(true); chime();} catch { /* no audio */ }
  };

  return (
    <div className={`officer ${status}`}>
      <header className="officer-head">
        <div className="brand"><strong>SafeSC</strong><span>Unit</span></div>
        <span className={`link ${linked ? 'up' : ''}`}>{linked ? 'Linked to dispatch' : 'Waiting for dispatch…'}</span>
      </header>
      <div className="officer-unit">
        <h1>{callsign}</h1>
        <span className={`unit-status ${status}`}>{UNIT_STATUS_LABEL[status]}</span>
      </div>
      {!alertsOn && <button type="button" className="ghost wide" onClick={enableAlerts}>Enable sound and vibration</button>}

      {!assignment ? (
        <div className="officer-idle">
          <div className="radar"><span /><span /><span /></div>
          <p>No assignment. Keep this page open; new dispatches arrive here.</p>
        </div>
      ) : (
        <section className={`assignment ${status === 'assigned' ? 'incoming' : ''}`}>
          <div className="assignment-top">
            <span className={`prio ${assignment.priority}`}>{assignment.priority}</span>
            <strong>{assignment.label}</strong>
          </div>
          {assignment.urgency && <span className={`urgency ${assignment.urgency}`}>{assignment.urgency}</span>}
          <h2>{assignment.place}</h2>
          {assignment.message && (
            <div className="agent-outreach">
              <small>From dispatch agent</small>
              <p>{assignment.message}</p>
            </div>
          )}
          {(assignment.address || assignment.floors) && (
            <p className="where">{[assignment.address, assignment.floors && `${assignment.floors} floors`].filter(Boolean).join(' · ')}</p>
          )}
          <p className="what">{assignment.observation}</p>
          <p className="why">{assignment.reason}</p>
          {assignment.support.map((s) => <p className="support" key={s}>{s}</p>)}
          {assignment.partners.length > 0 && <p className="partners">With {assignment.partners.join(', ')}</p>}
          {status === 'en_route' && eta !== undefined && <p className="eta">Arriving in {Math.ceil(eta)} s <small>(demo clock)</small></p>}
          <a className="ghost wide directions" href={directionsUrl(assignment.position)} target="_blank" rel="noreferrer">Directions in Google Maps</a>
          <div className="officer-actions">
            {status === 'assigned' && <button type="button" className="primary big" onClick={() => send('ack')}>Acknowledge</button>}
            {status === 'en_route' && <button type="button" className="primary big" onClick={() => send('arrived')}>Arrived</button>}
            {(status === 'on_scene' || status === 'en_route') && <button type="button" className="ghost big" onClick={() => send('clear')}>Clear</button>}
          </div>
        </section>
      )}
      <p className="fine">Demo unit. Officer identities and positions are simulated.</p>
    </div>
  );
}
