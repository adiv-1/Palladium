import {ColumnLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer} from '@deck.gl/layers';
import {HexagonLayer} from '@deck.gl/aggregation-layers';
import {Tile3DLayer, TripsLayer} from '@deck.gl/geo-layers';
import type {Color, Layer} from '@deck.gl/core';
import {cameraCone, distance} from './sim';
import type {Building, Camera, Dispatch, HistoryPoint, Incident, LngLat, Officer, Rack, Severity} from './types';
import {KEY_CATEGORIES} from './buildings';
import {CAMPUS_CENTER, offset} from './sim';

export const CARDINAL: Color = [204, 31, 52];
export const GOLD: Color = [255, 204, 0];
const SEVERITY_COLOR: Record<Severity, Color> = {
  HIGH_PRIORITY_REVIEW: CARDINAL,
  REVIEW_REQUIRED: [242, 110, 48],
  WATCH: GOLD,
};
export const severityColor = (s: Severity) => SEVERITY_COLOR[s];
const rgba = (c: Color, a: number): Color => { const [r, g, b] = c as unknown as number[]; return [r, g, b, a]; };

export type LayerToggles = {history: boolean; coverage: boolean; racks: boolean};

type Inputs = {
  time: number; // seconds, drives pulses and arcs
  incidents: Incident[];
  selectedId: string | null;
  officers: Officer[];
  cameras: Camera[];
  racks: Rack[];
  history: HistoryPoint[];
  hour: number | null;
  dispatches: Dispatch[];
  toggles: LayerToggles;
  google: {key: string; onCredits: (c: string) => void} | null;
  buildings: Building[];
  focusBuildingId: string | null;
  now: number; // Date.now(), for unit routes and drop-in timing
  orbiting: boolean;
  alert?: {center: LngLat; startedAt: number; radius: number; users: {id: string; position: LngLat; inside: boolean}[]} | null;
  extra?: Layer[]; // layers from other agents (intercept vector, etc.)
};

export function buildLayers(x: Inputs): Layer[] {
  const layers: Layer[] = [];
  const pulse = (0.5 + 0.5 * Math.sin(x.time * 4));

  if (x.google) {
    const {key, onCredits} = x.google;
    layers.push(new Tile3DLayer({
      id: 'google-3d-tiles',
      data: 'https://tile.googleapis.com/v1/3dtiles/root.json',
      loadOptions: {fetch: {headers: {'X-GOOG-API-KEY': key}}},
      operation: 'terrain+draw',
      onTilesetLoad: (tileset: any) => {
        // Google's terms require showing the data attributions of the tiles in view.
        tileset.options.onTraversalComplete = (selected: any[]) => {
          const credits = new Set<string>();
          for (const tile of selected) {
            const copyright = tile.content?.gltf?.asset?.copyright as string | undefined;
            copyright?.split(';').forEach((c) => c.trim() && credits.add(c.trim()));
          }
          onCredits([...credits].join('; '));
          return selected;
        };
      },
    }) as unknown as Layer);
  }

  // Radar sweep while idle: a rotating fan of fading wedges from the middle of campus.
  if (x.orbiting) {
    const sweep = (x.time * 50) % 360;
    const wedges = Array.from({length: 14}, (_, n) => {
      const a0 = ((sweep - n * 4) * Math.PI) / 180, a1 = ((sweep - (n + 1) * 4) * Math.PI) / 180;
      return {alpha: 26 * (1 - n / 14), polygon: [CAMPUS_CENTER, offset(CAMPUS_CENTER, Math.sin(a0) * 620, Math.cos(a0) * 620),
        offset(CAMPUS_CENTER, Math.sin(a1) * 620, Math.cos(a1) * 620)] as LngLat[]};
    });
    layers.push(new PolygonLayer<{alpha: number; polygon: LngLat[]}>({id: 'radar', data: wedges, getPolygon: (w) => w.polygon,
      stroked: false, getFillColor: (w) => [255, 204, 0, w.alpha], updateTriggers: {getPolygon: sweep, getFillColor: sweep}}) as unknown as Layer);
  }
  // Footprints, clickable for building facts.
  layers.push(new PolygonLayer<Building>({id: 'footprints', data: x.buildings.filter((b) => b.footprint.length > 3),
    getPolygon: (b) => b.footprint, stroked: true, filled: true, getFillColor: [0, 0, 0, 1], getLineColor: [120, 130, 145, 70],
    lineWidthMinPixels: 1, pickable: true, autoHighlight: true, highlightColor: [255, 204, 0, 60]}) as unknown as Layer);
  // Highlighted building (incident or clicked): glowing extruded outline that breathes.
  const focusBuilding = x.buildings.find((b) => b.id === x.focusBuildingId);
  if (focusBuilding && focusBuilding.footprint.length > 3) {
    const h = Number(focusBuilding.tags.height) || 4 * (Number(focusBuilding.tags['building:levels']) || 3);
    layers.push(new PolygonLayer<Building>({id: 'focus-building', data: [focusBuilding], getPolygon: (b) => b.footprint,
      extruded: true, wireframe: true, getElevation: h + 1, getFillColor: [255, 204, 0, 35 + 45 * pulse],
      getLineColor: [255, 214, 80, 230], material: false, updateTriggers: {getFillColor: pulse}}) as unknown as Layer);
  }
  // Key building labels (libraries, residences, recreation, parking, health) plus the focused one.
  const labelled = x.buildings.filter((b) => KEY_CATEGORIES.includes(b.category) || b.id === x.focusBuildingId);
  layers.push(new TextLayer<Building>({id: 'building-labels', data: labelled,
    getPosition: (b) => [b.position[0], b.position[1], (Number(b.tags.height) || 12) + 6] as any,
    getText: (b) => b.name, getSize: (b) => (b.id === x.focusBuildingId ? 15 : 11),
    getColor: (b) => (b.id === x.focusBuildingId ? [255, 214, 80, 255] : [175, 182, 194, 175]), fontFamily: 'DM Sans, sans-serif', fontWeight: 700,
    outlineWidth: 3, outlineColor: [18, 20, 23, 255], fontSettings: {sdf: true}, characterSet: 'auto',
    updateTriggers: {getSize: x.focusBuildingId, getColor: x.focusBuildingId}}) as unknown as Layer);

  if (x.toggles.history) {
    const points = x.hour === null ? x.history : x.history.filter((p) => p.hour === x.hour);
    layers.push(new HexagonLayer<HistoryPoint>({
      id: 'history-hex', data: points, getPosition: (p) => p.position, radius: 32, coverage: 0.86,
      extruded: true, elevationScale: x.hour === null ? 0.5 : 3, elevationRange: [0, 40], opacity: 0.38,
      colorRange: [[60, 52, 40], [110, 88, 40], [170, 130, 30], [230, 175, 20], [220, 100, 40], [204, 31, 52]],
      pickable: false, gpuAggregation: false,
    }) as unknown as Layer);
  }

  if (x.toggles.coverage) {
    layers.push(new PolygonLayer<Camera>({
      id: 'camera-cones', data: x.cameras, getPolygon: (c) => cameraCone(c), stroked: false,
      getFillColor: (c) => (c.watchedLive ? [255, 204, 0, 70] : [120, 190, 200, 26]),
      pickable: true,
    }) as unknown as Layer);
    layers.push(new ScatterplotLayer<Camera>({
      id: 'cameras', data: x.cameras, getPosition: (c) => c.position, radiusUnits: 'pixels',
      getRadius: (c) => (c.watchedLive ? 3.5 : 2), getFillColor: (c) => (c.watchedLive ? [255, 204, 0, 230] : [140, 200, 210, 150]),
    }) as unknown as Layer);
  }

  if (x.toggles.racks) {
    // A rack flares while an unreviewed rack-activity flag sits within 40 m of it.
    const flaring = new Set(x.racks.filter((r) => x.incidents.some((i) => i.type === 'rack_activity'
      && i.status === 'unreviewed' && distance(i.position, r.position) < 40)).map((r) => r.id));
    layers.push(new ScatterplotLayer<Rack>({
      id: 'racks', data: x.racks, getPosition: (r) => r.position, radiusUnits: 'meters', stroked: true,
      getRadius: (r) => (flaring.has(r.id) ? 3 + 4 * pulse : 2.4), lineWidthUnits: 'pixels', getLineWidth: 1,
      getFillColor: (r) => (flaring.has(r.id) ? [255, 204, 0, 120 + 100 * pulse] : [150, 160, 170, 110]),
      getLineColor: (r) => (flaring.has(r.id) ? [255, 230, 120, 255] : [190, 200, 210, 120]),
      updateTriggers: {getRadius: [pulse, [...flaring].join()], getFillColor: [pulse, [...flaring].join()], getLineColor: [...flaring].join()},
    }) as unknown as Layer);
  }

  const visible = x.incidents.filter((i) => i.status !== 'dismissed' && i.status !== 'resolved');
  const age = (i: Incident) => (x.now - i.createdAt) / 1000;
  // Beams drop in from the sky with a little overshoot.
  const drop = (i: Incident) => {const t = Math.min(1, Math.max(0, age(i)) / 1.1); return 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2;};
  const fresh = visible.filter((i) => age(i) < 2.6);
  if (fresh.length) {
    const waves = [...fresh.map((i) => ({i, delay: 0})), ...fresh.map((i) => ({i, delay: 0.35}))];
    layers.push(new ScatterplotLayer<{i: Incident; delay: number}>({id: 'shockwave', data: waves,
      getPosition: (w) => w.i.position, radiusUnits: 'meters', stroked: true, filled: false, lineWidthUnits: 'pixels', getLineWidth: 3,
      getRadius: (w) => Math.max(0, age(w.i) - w.delay) * 75,
      getLineColor: (w) => rgba(severityColor(w.i.severity), Math.max(0, 255 * (1 - (age(w.i) - w.delay) / 2.2))),
      updateTriggers: {getRadius: x.now, getLineColor: x.now}}) as unknown as Layer);
  }
  // Beams: pulse while unreviewed, hold steady once approved.
  layers.push(new ColumnLayer<Incident>({
    id: 'beam-glow', data: visible, getPosition: (i) => i.position, radius: 7, diskResolution: 20, extruded: true,
    getElevation: (i) => drop(i) * (i.status === 'approved' ? 140 : 110 + 50 * pulse),
    getFillColor: (i) => rgba(severityColor(i.severity), i.status === 'approved' ? 40 : 30 + 40 * pulse),
    material: false, updateTriggers: {getElevation: [pulse, x.now], getFillColor: pulse},
  }) as unknown as Layer);
  layers.push(new ColumnLayer<Incident>({
    id: 'beams', data: visible, getPosition: (i) => i.position, radius: 1.8, diskResolution: 12, extruded: true,
    getElevation: (i) => drop(i) * (i.status === 'approved' ? 140 : 110 + 50 * pulse),
    getFillColor: (i) => rgba(severityColor(i.severity), 235), material: false, pickable: true,
    updateTriggers: {getElevation: [pulse, x.now]},
  }) as unknown as Layer);
  layers.push(new ScatterplotLayer<Incident>({
    id: 'beam-rings', data: visible, getPosition: (i) => i.position, radiusUnits: 'meters', stroked: true, filled: false,
    getRadius: (i) => (i.status === 'approved' ? 9 : 6 + 22 * ((x.time * 0.8) % 1)),
    getLineColor: (i) => rgba(severityColor(i.severity), i.status === 'approved' ? 200 : 255 * (1 - ((x.time * 0.8) % 1))),
    lineWidthUnits: 'pixels', getLineWidth: (i) => (i.id === x.selectedId ? 3 : 1.6),
    updateTriggers: {getRadius: x.time, getLineColor: x.time, getLineWidth: x.selectedId},
  }) as unknown as Layer);

  const moving = x.officers.filter((o) => o.status === 'en_route' && o.route);
  if (moving.length) {
    layers.push(new PathLayer<Officer>({id: 'routes', data: moving, getPath: (o) => o.route!.path, getColor: [255, 204, 0, 55],
      widthUnits: 'pixels', getWidth: 3, capRounded: true}) as unknown as Layer);
    // Flowing comets along each route, looping.
    const flows = moving.flatMap((o) => [0, 1, 2].map((k) => ({o, k})));
    layers.push(new TripsLayer<{o: Officer; k: number}>({id: 'route-flow', data: flows, getPath: (f) => f.o.route!.path,
      getTimestamps: (f) => f.o.route!.path.map((_, i) => i + f.k * 9), getColor: [255, 220, 90], widthUnits: 'pixels', getWidth: 4,
      trailLength: 3, fadeTrail: true, capRounded: true, currentTime: (x.time * 12) % 30 + 3}) as unknown as Layer);
  }
  const busy = x.officers.filter((o) => o.status !== 'available');
  layers.push(new ScatterplotLayer<Officer>({id: 'unit-halo', data: busy, getPosition: (o) => o.position, radiusUnits: 'pixels',
    getRadius: (o) => (o.status === 'assigned' ? 12 + 10 * pulse : 10 + 12 * ((x.time * 1.2) % 1)),
    getFillColor: (o) => (o.status === 'on_scene' ? [95, 179, 168, 90] : [255, 204, 0, o.status === 'assigned' ? 60 + 90 * pulse : 90 * (1 - ((x.time * 1.2) % 1))]),
    updateTriggers: {getRadius: x.time, getFillColor: x.time}}) as unknown as Layer);
  layers.push(new ScatterplotLayer<Officer>({
    id: 'officers', data: x.officers, getPosition: (o) => o.position, radiusUnits: 'pixels', getRadius: 6,
    stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: (o) => (o.status === 'on_scene' ? [95, 179, 168, 255] : o.status !== 'available' ? [255, 204, 0, 255] : [235, 238, 242, 255]),
    getLineColor: [18, 20, 23, 255], pickable: true, updateTriggers: {getFillColor: x.officers.map((o) => o.status).join()},
  }) as unknown as Layer);
  layers.push(new TextLayer<Officer>({
    id: 'officer-labels', data: x.officers, getPosition: (o) => o.position, getText: (o) => o.callsign, getSize: 11,
    getColor: [220, 224, 230, 220], getPixelOffset: [0, -15], fontFamily: 'DM Sans, sans-serif', fontWeight: 700,
    outlineWidth: 3, outlineColor: [18, 20, 23, 255], fontSettings: {sdf: true},
  }) as unknown as Layer);

  if (x.dispatches.length) {
    const now = x.time;
    layers.push(new TripsLayer<Dispatch>({
      id: 'dispatch-arcs', data: x.dispatches, getPath: (d) => d.path,
      getTimestamps: (d) => d.path.map((_, i) => d.startedAt + (i / (d.path.length - 1)) * d.seconds),
      getColor: GOLD, widthUnits: 'pixels', getWidth: 5, capRounded: true, jointRounded: true,
      trailLength: 1.6, currentTime: now, fadeTrail: true,
    }) as unknown as Layer);
    layers.push(new TripsLayer<Dispatch>({
      id: 'dispatch-arc-trace', data: x.dispatches, getPath: (d) => d.path,
      getTimestamps: (d) => d.path.map((_, i) => d.startedAt + (i / (d.path.length - 1)) * d.seconds),
      getColor: [255, 204, 0, 70], widthUnits: 'pixels', getWidth: 2, trailLength: 1e6, currentTime: now, fadeTrail: false,
    }) as unknown as Layer);
  }
  if (x.alert) {
    const a = x.alert, age = x.time - a.startedAt, grow = Math.min(1, age / 2.4), r = a.radius * (1 - (1 - grow) ** 3);
    layers.push(new ScatterplotLayer({id: 'alert-zone', data: [a], getPosition: (d: typeof a) => d.center, radiusUnits: 'meters', getRadius: r,
      stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2.5, getFillColor: [204, 31, 52, 26 + 12 * pulse], getLineColor: [255, 90, 100, 230],
      updateTriggers: {getRadius: r, getFillColor: pulse}}) as unknown as Layer);
    layers.push(new ScatterplotLayer({id: 'alert-wave', data: [a], getPosition: (d: typeof a) => d.center, radiusUnits: 'meters', stroked: true, filled: false,
      lineWidthUnits: 'pixels', getLineWidth: 3, getRadius: (a.radius * ((age * 0.55) % 1)), getLineColor: [255, 120, 120, 200 * (1 - ((age * 0.55) % 1))],
      updateTriggers: {getRadius: age, getLineColor: age}}) as unknown as Layer);
    layers.push(new ScatterplotLayer({id: 'app-users', data: a.users, getPosition: (u: any) => u.position, radiusUnits: 'pixels', stroked: true, lineWidthUnits: 'pixels',
      getLineWidth: 1.5, getRadius: (u: any) => (u.inside && distance(u.position, a.center) <= r ? 5 + 2 * pulse : 3.2),
      getFillColor: (u: any) => (u.inside && distance(u.position, a.center) <= r ? [255, 204, 0, 255] : [150, 190, 255, u.inside ? 190 : 110]),
      getLineColor: [18, 20, 23, 255], updateTriggers: {getRadius: [r, pulse], getFillColor: r}}) as unknown as Layer);
  }
  if (x.extra) layers.push(...x.extra);
  return layers;
}
