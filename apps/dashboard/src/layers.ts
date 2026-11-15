import {ColumnLayer, PolygonLayer, ScatterplotLayer, TextLayer} from '@deck.gl/layers';
import {HexagonLayer} from '@deck.gl/aggregation-layers';
import {Tile3DLayer, TripsLayer} from '@deck.gl/geo-layers';
import type {Color, Layer} from '@deck.gl/core';
import {cameraCone, distance} from './sim';
import type {Camera, Dispatch, HistoryPoint, Incident, Officer, Rack, Severity} from './types';

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
      getRadius: (r) => (flaring.has(r.id) ? 6 + 10 * pulse : 2.4), lineWidthUnits: 'pixels', getLineWidth: 1,
      getFillColor: (r) => (flaring.has(r.id) ? [255, 204, 0, 120 + 100 * pulse] : [150, 160, 170, 110]),
      getLineColor: (r) => (flaring.has(r.id) ? [255, 230, 120, 255] : [190, 200, 210, 120]),
      updateTriggers: {getRadius: [pulse, [...flaring].join()], getFillColor: [pulse, [...flaring].join()], getLineColor: [...flaring].join()},
    }) as unknown as Layer);
  }

  const visible = x.incidents.filter((i) => i.status !== 'dismissed');
  // Beams: pulse while unreviewed, hold steady once approved.
  layers.push(new ColumnLayer<Incident>({
    id: 'beam-glow', data: visible, getPosition: (i) => i.position, radius: 7, diskResolution: 20, extruded: true,
    getElevation: (i) => (i.status === 'approved' ? 140 : 110 + 50 * pulse),
    getFillColor: (i) => rgba(severityColor(i.severity), i.status === 'approved' ? 40 : 30 + 40 * pulse),
    material: false, updateTriggers: {getElevation: pulse, getFillColor: pulse},
  }) as unknown as Layer);
  layers.push(new ColumnLayer<Incident>({
    id: 'beams', data: visible, getPosition: (i) => i.position, radius: 1.8, diskResolution: 12, extruded: true,
    getElevation: (i) => (i.status === 'approved' ? 140 : 110 + 50 * pulse),
    getFillColor: (i) => rgba(severityColor(i.severity), 235), material: false, pickable: true,
    updateTriggers: {getElevation: pulse},
  }) as unknown as Layer);
  layers.push(new ScatterplotLayer<Incident>({
    id: 'beam-rings', data: visible, getPosition: (i) => i.position, radiusUnits: 'meters', stroked: true, filled: false,
    getRadius: (i) => (i.status === 'approved' ? 9 : 6 + 22 * ((x.time * 0.8) % 1)),
    getLineColor: (i) => rgba(severityColor(i.severity), i.status === 'approved' ? 200 : 255 * (1 - ((x.time * 0.8) % 1))),
    lineWidthUnits: 'pixels', getLineWidth: (i) => (i.id === x.selectedId ? 3 : 1.6),
    updateTriggers: {getRadius: x.time, getLineColor: x.time, getLineWidth: x.selectedId},
  }) as unknown as Layer);

  layers.push(new ScatterplotLayer<Officer>({
    id: 'officers', data: x.officers, getPosition: (o) => o.position, radiusUnits: 'pixels', getRadius: 6,
    stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getFillColor: (o) => (o.busyWith ? [255, 204, 0, 255] : [235, 238, 242, 255]),
    getLineColor: [18, 20, 23, 255], pickable: true, updateTriggers: {getFillColor: x.officers.map((o) => o.busyWith).join()},
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
  return layers;
}
