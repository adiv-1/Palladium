import {useEffect, useMemo, useRef, useState} from 'react';
import {openBus, type AlertMsg, type Bus} from './bus';
import {distance} from './sim';
import {Logo} from './Logo';
import type {LngLat} from './types';

type Place = {name: string; position: LngLat};
type Received = {msg: AlertMsg; cleared: boolean};

// The phone view for students: shows a safety alert only when the phone is inside the alert radius.
export default function StudentApp({room}: {room: string}) {
  const [places, setPlaces] = useState<Place[]>([]);
  const [placeName, setPlaceName] = useState('Leavey Library');
  const [gps, setGps] = useState<LngLat | null>(null);
  const [gpsError, setGpsError] = useState('');
  const [received, setReceived] = useState<Received[]>([]);
  const [linked, setLinked] = useState(false);
  const bus = useRef<Bus | null>(null);
  const audio = useRef<AudioContext | null>(null);
  const id = useMemo(() => {
    try {
      const saved = sessionStorage.getItem('safesc-student-id');
      if (saved) return saved;
      const fresh = `student-${Math.random().toString(36).slice(2, 8)}`;
      sessionStorage.setItem('safesc-student-id', fresh);
      return fresh;
    } catch {return `student-${Math.random().toString(36).slice(2, 8)}`;}
  }, []);

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}osm/buildings.json`).then((r) => r.json()).then((b) => {
      const list: Place[] = b.buildings.map((x: any) => ({name: x.name, position: [x.lng, x.lat] as LngLat}));
      setPlaces(list.sort((a, c) => a.name.localeCompare(c.name)));
    }).catch(() => undefined);
  }, []);

  const position: LngLat | null = gps ?? places.find((p) => p.name === placeName)?.position ?? null;
  const posRef = useRef(position);
  posRef.current = position;

  const chime = () => {
    navigator.vibrate?.([300, 150, 300, 150, 600]);
    const ctx = audio.current;
    if (!ctx) return;
    [660, 880, 660, 880].forEach((f, n) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.frequency.value = f; osc.type = 'square';
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + n * 0.22);
      gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + n * 0.22 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + n * 0.22 + 0.2);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + n * 0.22); osc.stop(ctx.currentTime + n * 0.22 + 0.22);
    });
  };

  useEffect(() => {
    bus.current = openBus(room, (m) => {
      if (m.type !== 'alert') return;
      const msg = m.alert;
      setReceived((list) => {
        if (msg.kind === 'clear') return list.map((r) => (r.msg.id === msg.id.replace('clear-', 'alert-') ? {...r, cleared: true} : r));
        if (list.some((r) => r.msg.id === msg.id)) return list;
        if (posRef.current && distance(posRef.current, msg.center) <= msg.radius) chime();
        return [{msg, cleared: false}, ...list];
      });
    });
    const ping = () => {bus.current?.send({type: 'presence', unitId: id}); setLinked(true);};
    ping();
    const timer = setInterval(ping, 2000);
    return () => {clearInterval(timer); bus.current?.close();};
  }, [room, id]);

  const useGps = () => {
    setGpsError('');
    if (!navigator.geolocation) {setGpsError('This browser has no location access.'); return;}
    navigator.geolocation.watchPosition((p) => setGps([p.coords.longitude, p.coords.latitude]), (e) => setGpsError(e.message), {enableHighAccuracy: true});
  };

  const rows = received.map((r) => ({...r, away: position ? distance(position, r.msg.center) : Infinity}));
  const active = rows.filter((r) => !r.cleared && r.away <= r.msg.radius);
  const quiet = rows.filter((r) => r.cleared || r.away > r.msg.radius);

  return (
    <div className={`student-app ${active.length ? 'danger' : ''}`}>
      <header><div className="logo"><Logo variant="shield" size={30} /><strong>Palladium</strong></div><span className={`link ${linked ? 'up' : ''}`}>{linked ? 'Connected' : 'Connecting…'}</span></header>
      <h1>{active.length ? 'Safety alert near you' : 'You are all set'}</h1>
      <p className="lede">{active.length ? 'Stay calm and follow the steps below.' : 'Keep this page open. If something happens within 200 m of you, an alert appears here.'}</p>

      {active.map(({msg, away}) => (
        <article className="alert-card" key={msg.id}>
          <span className="eyebrow">{new Date(msg.at).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})} · {Math.round(away)} m from you</span>
          <h2>{msg.title}</h2>
          <p>{msg.message}</p>
          <ul><li>Move away from {msg.place}.</li><li>Go into a staffed, well-lit building.</li><li>Do not approach or film the incident.</li></ul>
        </article>
      ))}

      <section className="where">
        <h3>Your location <small>(demo)</small></h3>
        <select value={gps ? '' : placeName} onChange={(e) => {setGps(null); setPlaceName(e.target.value);}} aria-label="Pretend you are at">
          {gps && <option value="">Using GPS</option>}
          {places.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
        <div className="row"><button className="ghost" onClick={useGps}>Use my real location</button>
          <button className="ghost" onClick={() => {try {audio.current = new AudioContext(); chime();} catch { /* no audio */ }}}>Test alert sound</button></div>
        {gpsError && <p className="error">{gpsError}</p>}
      </section>

      {quiet.length > 0 && (
        <section className="log"><h3>Other alerts</h3>
          {quiet.map(({msg, away, cleared}) => (
            <p key={msg.id}>{cleared ? 'All clear' : `Outside your ${msg.radius} m zone (${Math.round(away)} m away), not shown to you`}: {msg.place}</p>
          ))}
        </section>
      )}
      <p className="fine">Demo app. Locations and alerts are simulated.</p>
    </div>
  );
}
