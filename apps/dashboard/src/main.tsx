import React from 'react';
import {createRoot} from 'react-dom/client';
import {setWorkerUrl} from 'maplibre-gl';
// MapLibre 6 loads its worker as an ES module next to a shared chunk; Vite's dependency
// pre-bundling breaks that relative path, so let Vite bundle the worker and hand over its URL.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import App from './App';

setWorkerUrl(maplibreWorkerUrl);

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
