import {outlineRGBA} from './theme';
// deck.gl layers for the Dynamic Intercept Vector. Heuristic prediction from a SIMULATED heading (see intercept.ts).
// startedAt and time are both in seconds on the same clock (the dashboard's animation time); t = time - startedAt
// is "seconds since the incident was confirmed". Unit routes only animate once `approved` is true.
import {PathLayer, PolygonLayer, ScatterplotLayer, TextLayer} from '@deck.gl/layers';
import {TripsLayer} from '@deck.gl/geo-layers';
import type {Color, Layer} from '@deck.gl/core';
import type {Branch, ChokePoint, InterceptPlan} from './intercept';
import type {LngLat} from './types';

const CARDINAL: Color = [204, 31, 52];
const GOLD: Color = [255, 204, 0];
const TEAL: Color = [95, 179, 168];
const rgba = (c: Color, a: number): Color => {const [r, g, b] = c as unknown as number[]; return [r, g, b, a];};

// Position along a timed path at time t (clamped).
function at(path: LngLat[], ts: number[], t: number): LngLat {
  if (t <= ts[0]) return path[0];
  for (let i = 1; i < path.length; i++) {
    if (t <= ts[i]) {const f = (t - ts[i - 1]) / Math.max(1e-6, ts[i] - ts[i - 1]); return [path[i - 1][0] + (path[i][0] - path[i - 1][0]) * f, path[i - 1][1] + (path[i][1] - path[i - 1][1]) * f];}
  }
  return path[path.length - 1];
}
const cumulative = (p: LngLat[]) => {
  const k = Math.cos((p[0][1] * Math.PI) / 180), out = [0];
  for (let i = 1; i < p.length; i++) out.push(out[i - 1] + Math.hypot((p[i][0] - p[i - 1][0]) * k, p[i][1] - p[i - 1][1]) * 111320);
  return out;
};

export function interceptLayers(plan: InterceptPlan, startedAt: number, time: number, approved = true): Layer[] {
  const t = Math.max(0, time - startedAt), H = plan.horizonS, layers: Layer[] = [];
  const pulse = 0.5 + 0.5 * Math.sin(time * 4), ring = (time * 0.9) % 1;

  // Suspect branches: faint full path plus a red comet racing along it, thickness by probability.
  layers.push(new PathLayer<Branch>({id: 'icpt-branch-trace', data: plan.predicted, getPath: (b) => b.path,
    getColor: (b) => rgba(CARDINAL, 40 + 110 * b.prob), widthUnits: 'pixels', getWidth: (b) => 2 + 6 * b.prob, capRounded: true, jointRounded: true}) as unknown as Layer);
  layers.push(new TripsLayer<Branch>({id: 'icpt-branches', data: plan.predicted, getPath: (b) => b.path, getTimestamps: (b) => b.t,
    getColor: (b) => rgba(CARDINAL, 255), widthUnits: 'pixels', getWidth: (b) => 3 + 9 * b.prob, trailLength: Math.max(10, H * 0.4), fadeTrail: true,
    capRounded: true, jointRounded: true, currentTime: Math.min(t, H)}) as unknown as Layer);
  const main = plan.predicted[0];
  if (main) {
    const head = at(main.path, main.t, Math.min(t, H));
    layers.push(new ScatterplotLayer<LngLat>({id: 'icpt-suspect', data: [head], getPosition: (p) => p, radiusUnits: 'pixels', getRadius: 6 + 3 * pulse,
      getFillColor: [229, 42, 64, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getLineColor: [255, 255, 255, 230],
      updateTriggers: {getPosition: t, getRadius: pulse}}) as unknown as Layer);
  }

  // Predicted exit: pulsing ring and label.
  if (plan.exit) {
    layers.push(new ScatterplotLayer<LngLat>({id: 'icpt-exit-ring', data: [plan.exit.position], getPosition: (p) => p, radiusUnits: 'meters', stroked: true, filled: false,
      lineWidthUnits: 'pixels', getLineWidth: 3, getRadius: 6 + 34 * ring, getLineColor: rgba(CARDINAL, 255 * (1 - ring)),
      updateTriggers: {getRadius: ring, getLineColor: ring}}) as unknown as Layer);
    layers.push(new ScatterplotLayer<LngLat>({id: 'icpt-exit-dot', data: [plan.exit.position], getPosition: (p) => p, radiusUnits: 'pixels', getRadius: 5,
      getFillColor: [229, 42, 64, 255]}) as unknown as Layer);
    layers.push(new TextLayer<LngLat>({id: 'icpt-exit-label', data: [plan.exit.position], getPosition: (p) => p, getText: () => 'Predicted exit', getSize: 12,
      getColor: [255, 190, 196, 255], getPixelOffset: [0, -20], fontFamily: 'Inter, sans-serif', fontWeight: 700, outlineWidth: 3, outlineColor: outlineRGBA(),
      fontSettings: {sdf: true}}) as unknown as Layer);
  }

  // Camera hand-off: cones brighten in order of arrival, with a short flash when the suspect enters.
  if (plan.cameraCones.length) {
    const cones = plan.cameraCones.map((polygon, i) => ({polygon, etaS: plan.cameraHandoff[i].etaS}));
    const on = (c: {etaS: number}) => t >= c.etaS - 2;
    layers.push(new PolygonLayer<{polygon: LngLat[]; etaS: number}>({id: 'icpt-cones', data: cones, getPolygon: (c) => c.polygon, stroked: true, lineWidthMinPixels: 1,
      getFillColor: (c) => (on(c) ? [255, 214, 80, Math.max(70, 190 - 60 * Math.max(0, t - c.etaS)) | 0] : [120, 190, 200, 34]),
      getLineColor: (c) => (on(c) ? [255, 230, 120, 255] : [120, 190, 200, 80]), updateTriggers: {getFillColor: Math.floor(t * 4), getLineColor: Math.floor(t)}}) as unknown as Layer);
  }

  // Unit routes: proposed (dim, static) until approved, then gold comets.
  if (plan.chokePoints.length) {
    layers.push(new PathLayer<ChokePoint>({id: 'icpt-unit-trace', data: plan.chokePoints, getPath: (c) => c.path, getColor: rgba(GOLD, approved ? 80 : 130),
      widthUnits: 'pixels', getWidth: approved ? 3 : 2.5, capRounded: true, jointRounded: true}) as unknown as Layer);
    if (approved) {
      layers.push(new TripsLayer<ChokePoint>({id: 'icpt-unit-routes', data: plan.chokePoints, getPath: (c) => c.path, getTimestamps: (c) => c.pathT, getColor: GOLD,
        widthUnits: 'pixels', getWidth: 5, trailLength: 8, fadeTrail: true, capRounded: true, jointRounded: true, currentTime: t}) as unknown as Layer);
    }
    layers.push(new ScatterplotLayer<ChokePoint>({id: 'icpt-choke-rings', data: plan.chokePoints, getPosition: (c) => c.position, radiusUnits: 'meters', stroked: true, filled: false,
      lineWidthUnits: 'pixels', getLineWidth: 2.5, getRadius: 6 + 22 * ring, getLineColor: rgba(GOLD, 255 * (1 - ring)),
      updateTriggers: {getRadius: ring, getLineColor: ring}}) as unknown as Layer);
    layers.push(new ScatterplotLayer<ChokePoint>({id: 'icpt-choke-dots', data: plan.chokePoints, getPosition: (c) => c.position, radiusUnits: 'pixels', getRadius: 7,
      getFillColor: [255, 204, 0, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getLineColor: [18, 20, 23, 255]}) as unknown as Layer);
    layers.push(new TextLayer<ChokePoint>({id: 'icpt-choke-labels', data: plan.chokePoints, getPosition: (c) => c.position,
      getText: (c) => `${c.unit.callsign}  +${Math.round(c.marginS)} s`, getSize: 12, getColor: [255, 226, 120, 255], getPixelOffset: [0, -20],
      fontFamily: 'Inter, sans-serif', fontWeight: 700, outlineWidth: 3, outlineColor: outlineRGBA(), fontSettings: {sdf: true}, characterSet: 'auto'}) as unknown as Layer);
  }

  // Victim safe-haven route: teal, with light flowing toward the destination.
  const v = plan.victimRoute;
  if (v) {
    const cum = cumulative(v.path), len = cum[cum.length - 1] || 1, loop = (time * 40) % (len + 60);
    layers.push(new PathLayer<{path: LngLat[]}>({id: 'icpt-victim-route', data: [{path: v.path}], getPath: (d) => d.path, getColor: rgba(TEAL, 110 + 90 * pulse),
      widthUnits: 'pixels', getWidth: 5, capRounded: true, jointRounded: true, updateTriggers: {getColor: pulse}}) as unknown as Layer);
    layers.push(new TripsLayer<{path: LngLat[]}>({id: 'icpt-victim-flow', data: [{path: v.path}], getPath: (d) => d.path, getTimestamps: () => cum, getColor: [170, 240, 228],
      widthUnits: 'pixels', getWidth: 6, trailLength: 50, fadeTrail: true, capRounded: true, currentTime: loop}) as unknown as Layer);
    layers.push(new ScatterplotLayer<LngLat>({id: 'icpt-haven-dot', data: [v.path[v.path.length - 1]], getPosition: (p) => p, radiusUnits: 'pixels', getRadius: 8 + 2 * pulse,
      getFillColor: [95, 179, 168, 255], stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2, getLineColor: [255, 255, 255, 230], updateTriggers: {getRadius: pulse}}) as unknown as Layer);
    layers.push(new TextLayer<LngLat>({id: 'icpt-haven-label', data: [v.path[v.path.length - 1]], getPosition: (p) => p, getText: () => `Safe haven: ${v.to.name}`, getSize: 12,
      getColor: [170, 240, 228, 255], getPixelOffset: [0, -22], fontFamily: 'Inter, sans-serif', fontWeight: 700, outlineWidth: 3, outlineColor: outlineRGBA(),
      fontSettings: {sdf: true}, characterSet: 'auto'}) as unknown as Layer);
  }
  return layers;
}
