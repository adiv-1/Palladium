import React, {Suspense, lazy} from 'react';
import {createRoot} from 'react-dom/client';
import {setWorkerUrl} from 'maplibre-gl';
// MapLibre 6 loads its worker as an ES module next to a shared chunk; Vite's dependency
// pre-bundling breaks that relative path, so let Vite bundle the worker and hand over its URL.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import './theme.css';
import './style.css';
import './dispatch.css';
import './agent.css';
import './pages.css';
import './embedded.css';
import {useRoute} from './Nav';
import {initTheme} from './theme';
import './command.css';

setWorkerUrl(maplibreWorkerUrl);
initTheme();
document.documentElement.classList.toggle('embedded', new URLSearchParams(location.search).get('embedded') === '1');

const Trends = lazy(() => import('./Trends'));
const Incidents = lazy(() => import('./Incidents'));
const About = lazy(() => import('./About'));
const Live = lazy(() => import('./App'));
const OfficerView = lazy(() => import('./Officer'));
const StudentApp = lazy(() => import('./StudentApp'));

// Pages live behind the hash (#/trends, #/incidents, #/live, #/about) so the static gateway needs no routes.
// ?unit=...&room=... opens the officer phone view; ?app=1&room=... opens the student alert app.
function Root() {
  const params = new URLSearchParams(location.search);
  const route = useRoute();
  const unit = params.get('unit'), room = params.get('room');
  let page: React.ReactNode;
  const embedded = params.get('embedded') === '1';
  // Inside the reviewer site the frame opens on Live unless the parent asks for another page (#/trends ...).
  const effective = embedded && !location.hash ? 'live' : route;
  if (unit && room) page = <OfficerView unitId={unit} room={room} />;
  else if (params.get('app')) {
    let saved = '';
    try {saved = localStorage.getItem('safesc-dispatch-room') ?? '';} catch { /* storage blocked */ }
    page = <StudentApp room={room ?? saved} />;
  } else page = effective === 'live' ? <Live embedded={embedded} /> : effective === 'incidents' ? <Incidents /> : effective === 'about' ? <About /> : <Trends />;
  return <Suspense fallback={<div className="boot"><i />Loading Palladium…</div>}>{page}</Suspense>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><Root /></React.StrictMode>);
