import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import DeckGL from '@deck.gl/react';
import {FlyToInterpolator, type MapViewState, type PickingInfo} from '@deck.gl/core';
import {Map} from 'react-map-gl/maplibre';
import {buildLayers, type LayerToggles} from './layers';
import {darkCampusStyle} from './mapStyle';
import {arcPath, CAMPUS_CENTER, demoIncident, makeCameras, makeOfficers, recommend, simulatedHistory} from './sim';
import {existingIncidents, fromSafeSC, login, review, subscribe, type CascadeStats} from './live';
import {BottomBar, ConnectDialog, IncidentDetail, IncidentQueue, Legend, StatusStrip} from './Panels';
import type {Building, Dispatch, Funnel, HistoryPoint, Incident, Officer, Rack} from './types';

const GOOGLE_KEY = (import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined) || '';
const HOME: MapViewState = {longitude: CAMPUS_CENTER[0], latitude: CAMPUS_CENTER[1], zoom: 16.3, pitch: 58, bearing: -20};
const fly = new FlyToInterpolator();

type Mode = 'orbit' | 'focus' | 'dispatch';

export default function App() {
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [racks, setRacks] = useState<Rack[]>([]);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historyReal, setHistoryReal] = useState(false);
  const [officers, setOfficers] = useState<Officer[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dispatches, setDispatches] = useState<Dispatch[]>([]);
  const [viewState, setViewState] = useState<MapViewState & Record<string, unknown>>(HOME);
  const [time, setTime] = useState(0);
  const [mode, setMode] = useState<Mode>('orbit');
  const [mapMode, setMapMode] = useState<'google' | 'maplibre'>(GOOGLE_KEY ? 'google' : 'maplibre');
  const [credits, setCredits] = useState('');
  const [toggles, setToggles] = useState<LayerToggles>({history: false, coverage: true, racks: true});
  const [hour, setHour] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [cascade, setCascade] = useState<CascadeStats | null>(null);
  const [demoFunnel, setDemoFunnel] = useState({frames: 0, candidates: 0, vlmChecks: 0});

  const lastInteraction = useRef(0);
  const timeRef = useRef(0);
  const incidentsRef = useRef<Incident[]>([]);
  incidentsRef.current = incidents;
  const officersRef = useRef<Officer[]>([]);
  officersRef.current = officers;

  const cameras = useMemo(() => (buildings.length ? makeCameras(buildings) : []), [buildings]);
  const aiCameras = cameras.filter((c) => !c.watchedLive).length;
  const watchedLive = cameras.length - aiCameras;

  // ---- data ------------------------------------------------------------------------
  useEffect(() => {
    (async () => {
      const [r, b] = await Promise.all([fetch('/data/racks.json').then((x) => x.json()), fetch('/data/buildings.json').then((x) => x.json())]);
      const rackList: Rack[] = r.racks.map((x: any) => ({id: x.id, position: [x.lng, x.lat], capacity: x.capacity}));
      const buildingList: Building[] = b.buildings.map((x: any) => ({id: x.id, name: x.name, position: [x.lng, x.lat]}));
      setRacks(rackList);
      setBuildings(buildingList);
      setOfficers(makeOfficers(buildingList));
      try {
        const h = await fetch('/data/history.json');
        if (!h.ok || !(h.headers.get('content-type') || '').includes('json')) throw new Error('none');
        const rows = await h.json();
        setHistory(rows.map((p: any) => ({position: [p.lng, p.lat], hour: p.hour, type: p.type})));
        setHistoryReal(true);
      } catch {
        setHistory(simulatedHistory(buildingList, rackList));
      }
    })();
  }, []);

  // ---- animation clock and idle orbit -----------------------------------------------
  useEffect(() => {
    let frame = 0, last = performance.now(), lastRender = 0;
    const start = last;
    const loop = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      timeRef.current = (now - start) / 1000;
      if (now - lastRender > 33) {
        lastRender = now;
        setTime(timeRef.current);
      }
      setMode((m) => {
        if (m === 'orbit' && Date.now() - lastInteraction.current > 10000) {
          setViewState((v) => (v.transitionDuration ? v : {...v, bearing: (v.bearing ?? 0) + 3 * dt}));
        }
        return m;
      });
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, []);

  // ---- simulated funnel in demo mode ----------------------------------------------------
  useEffect(() => {
    if (cascade) return;
    const id = setInterval(() => setDemoFunnel((f) => ({
      frames: f.frames + aiCameras * 5,
      candidates: f.candidates + (Math.random() < 0.08 ? 1 : 0),
      vlmChecks: f.vlmChecks + (Math.random() < 0.05 ? 1 : 0),
    })), 1000);
    return () => clearInterval(id);
  }, [aiCameras, cascade]);

  // ---- history playback ------------------------------------------------------------------
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setHour((h) => (h === null ? 0 : (h + 1) % 24)), 1200);
    return () => clearInterval(id);
  }, [playing]);

  const showToast = useCallback((text: string) => {
    setToast(text);
    setTimeout(() => setToast((t) => (t === text ? null : t)), 4500);
  }, []);

  const flyTo = useCallback((v: Partial<MapViewState>, duration: number) => {
    setViewState((cur) => ({...cur, ...v, transitionDuration: duration, transitionInterpolator: fly}));
  }, []);

  const focus = useCallback((incident: Incident) => {
    setSelectedId(incident.id);
    setMode('focus');
    flyTo({longitude: incident.position[0], latitude: incident.position[1], zoom: 18.3, pitch: 62}, 2000);
  }, [flyTo]);

  const backToOrbit = useCallback(() => {
    setMode('orbit');
    flyTo({longitude: HOME.longitude, latitude: HOME.latitude, zoom: HOME.zoom, pitch: HOME.pitch}, 2200);
  }, [flyTo]);

  const addIncident = useCallback((incident: Incident) => {
    setIncidents((list) => (list.some((i) => i.id === incident.id) ? list : [incident, ...list]));
    if (incident.status === 'unreviewed') focus(incident);
  }, [focus]);

  const stageDemo = useCallback(() => {
    if (!buildings.length) return;
    addIncident(demoIncident(buildings, racks, cameras));
    setDemoFunnel((f) => ({...f, candidates: f.candidates + 3, vlmChecks: f.vlmChecks + 1}));
  }, [buildings, racks, cameras, addIncident]);

  // ---- keyboard ----------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT') return;
      if (e.key === 'd' || e.key === 'D') stageDemo();
      if ((e.key === 'm' || e.key === 'M') && GOOGLE_KEY) setMapMode((m) => (m === 'google' ? 'maplibre' : 'google'));
      if (e.key === 'Escape') {setSelectedId(null); backToOrbit();}
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [stageDemo, backToOrbit]);

  const selected = incidents.find((i) => i.id === selectedId) ?? null;
  const recommendation = selected && selected.status === 'unreviewed' ? recommend(officers, selected.position) ?? null : null;

  // ---- decisions -----------------------------------------------------------------------------
  const approve = useCallback(async () => {
    const incident = incidentsRef.current.find((i) => i.id === selectedId);
    if (!incident || incident.status !== 'unreviewed') return;
    const rec = recommend(officersRef.current, incident.position);
    if (!rec) {showToast('No available officer (simulated roster)'); return;}
    const {officer} = rec;
    if (incident.origin === 'live') {
      try {
        await review(incident.id, 'confirmed', `Approved on dashboard; dispatched ${officer.callsign} (simulated officer)`);
      } catch (e) {
        showToast(`Could not record approval: ${(e as Error).message}`);
        return;
      }
    }
    setIncidents((list) => list.map((i) => (i.id === incident.id ? {...i, status: 'approved', officerId: officer.id, decidedAt: Date.now()} : i)));
    setOfficers((list) => list.map((o) => (o.id === officer.id ? {...o, busyWith: incident.id} : o)));
    setDispatches((list) => [...list, {incidentId: incident.id, officerId: officer.id, path: arcPath(officer.position, incident.position), startedAt: timeRef.current, seconds: 2.5}]);
    setMode('dispatch');
    flyTo({longitude: (officer.position[0] + incident.position[0]) / 2, latitude: (officer.position[1] + incident.position[1]) / 2, zoom: 16.9, pitch: 55}, 1600);
    showToast(`Ping sent to ${officer.callsign} (simulated officer)`);
    setTimeout(() => {
      setOfficers((list) => list.map((o) => (o.id === officer.id ? {...o, position: incident.position, busyWith: undefined} : o)));
      setDispatches((list) => list.filter((d) => d.incidentId !== incident.id));
      setSelectedId((id) => (id === incident.id ? null : id));
      backToOrbit();
    }, 7000);
  }, [selectedId, flyTo, showToast, backToOrbit]);

  const dismiss = useCallback(async () => {
    const incident = incidentsRef.current.find((i) => i.id === selectedId);
    if (!incident) return;
    if (incident.origin === 'live') {
      try {await review(incident.id, 'dismissed', 'Dismissed on dashboard');}
      catch (e) {showToast(`Could not record dismissal: ${(e as Error).message}`); return;}
    }
    setIncidents((list) => list.map((i) => (i.id === incident.id ? {...i, status: 'dismissed', decidedAt: Date.now()} : i)));
    setSelectedId(null);
    backToOrbit();
  }, [selectedId, showToast, backToOrbit]);

  // ---- live SafeSC ------------------------------------------------------------------------------
  const unsubscribe = useRef<(() => void) | null>(null);
  useEffect(() => () => unsubscribe.current?.(), []);
  const connect = useCallback(async (token: string) => {
    await login(token);
    const existing = await existingIncidents();
    setIncidents((list) => [...existing.map((raw) => fromSafeSC(raw, cameras)), ...list.filter((i) => i.origin === 'demo')]);
    unsubscribe.current?.();
    unsubscribe.current = subscribe({
      incident: (raw) => addIncident(fromSafeSC(raw, cameras)),
      source: (raw) => {if (raw?.telemetry?.cascade) setCascade(raw.telemetry.cascade);},
      status: setLiveStatus,
    });
    setLiveStatus('connected');
    setConnectOpen(false);
    showToast(`Connected to SafeSC: ${existing.length} incidents loaded`);
  }, [cameras, addIncident, showToast]);

  // ---- counters -----------------------------------------------------------------------------------
  const approvedCount = incidents.filter((i) => i.status === 'approved').length;
  const funnel: Funnel = cascade
    ? {frames: cascade.frames, candidates: Object.values(cascade.candidates || {}).reduce((a, b) => a + b, 0),
       vlmChecks: cascade.tier2_vlm_calls, alerts: incidents.length, approved: approvedCount}
    : {...demoFunnel, alerts: incidents.length, approved: approvedCount};
  const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  const hoursToday = (Date.now() - midnight.getTime()) / 3.6e6;
  const costToday = (aiCameras / 40) * 0.6 * hoursToday;

  const google = mapMode === 'google' && GOOGLE_KEY ? {key: GOOGLE_KEY, onCredits: setCredits} : null;
  const layers = buildLayers({time, incidents, selectedId, officers, cameras, racks, history, hour, dispatches, toggles, google});

  const tooltip = ({object, layer}: PickingInfo) => {
    if (!object || !layer) return null;
    if (layer.id === 'officers') return {text: `${object.callsign} · ${object.mode} · simulated officer${object.busyWith ? ' · dispatched' : ''}`};
    if (layer.id === 'camera-cones') return {text: `${object.id} near ${object.near} · ${object.watchedLive ? 'watched live' : 'AI triaged'} · simulated position`};
    if (layer.id === 'beams') return {text: `${object.place}: ${object.observation}`};
    return null;
  };

  return (
    <div className="app">
      <DeckGL
        viewState={viewState}
        controller={{inertia: 300}}
        layers={layers}
        getTooltip={tooltip as any}
        onViewStateChange={({viewState: v, interactionState}) => {
          if (interactionState?.isDragging || interactionState?.isZooming || interactionState?.isPanning || interactionState?.isRotating) {
            lastInteraction.current = Date.now();
          }
          setViewState(v as MapViewState);
        }}
        onClick={({object, layer}) => {
          if (layer?.id === 'beams' && object) focus(object as Incident);
        }}
      >
        {mapMode === 'maplibre' && <Map mapStyle={darkCampusStyle} reuseMaps attributionControl={{compact: true}} />}
      </DeckGL>
      {mapMode === 'google' && <div className="credits">Google · {credits}</div>}
      <StatusStrip camerasTriaged={aiCameras} watchedLive={watchedLive} vlmCalls={funnel.vlmChecks} costToday={costToday}
        costProvenance="estimate" mapMode={mapMode} googleAvailable={!!GOOGLE_KEY}
        onToggleMap={() => GOOGLE_KEY && setMapMode((m) => (m === 'google' ? 'maplibre' : 'google'))} liveStatus={liveStatus} />
      <IncidentQueue incidents={incidents} selectedId={selectedId} onSelect={(id: string) => {const i = incidents.find((x) => x.id === id); if (i) focus(i);}}
        onDemo={stageDemo} onConnect={() => setConnectOpen(true)} />
      <IncidentDetail incident={selected} recommendation={recommendation} onApprove={approve} onDismiss={dismiss}
        onClose={() => {setSelectedId(null); backToOrbit();}} />
      <BottomBar hour={hour} setHour={setHour} playing={playing} setPlaying={setPlaying} funnel={funnel} toggles={toggles} setToggles={setToggles}
        />
      <Legend />
      {toast && <div className="toast">{toast}</div>}
      <ConnectDialog open={connectOpen} onClose={() => setConnectOpen(false)} onSubmit={connect} />
    </div>
  );
}
