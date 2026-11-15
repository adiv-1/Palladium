// Building names on the 3D maps. Every named OSM building gets a label; a collision filter hides the ones
// that would overlap, keeping landmarks (libraries, residences, recreation, health) and large buildings first.
// Zooming in reveals more names. Hovering a footprint shows its full name and type.
import {useMemo} from 'react';
import {WebMercatorViewport, type Layer, type MapViewState} from '@deck.gl/core';
import {PolygonLayer, TextLayer} from '@deck.gl/layers';
import {CATEGORY_LABEL, KEY_CATEGORIES} from './buildings';
import {onCampus} from './sim';
import type {Building} from './types';


// Footprint area in square metres, enough to rank buildings by size.
function area(b: Building) {
  const ring = b.footprint;
  if (ring.length < 4) return 0;
  const k = 111320 * Math.cos((b.position[1] * Math.PI) / 180);
  let s = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) s += (ring[j][0] * k) * (ring[i][1] * 110540) - (ring[i][0] * k) * (ring[j][1] * 110540);
  return Math.abs(s / 2);
}

// Short, readable label: OSM short name for very long names, and no trailing "Building".
export function labelFor(b: Building) {
  // "School of Cinematic Arts - SCA" and its siblings: the campus building code is what people use.
  const code = b.name.match(/\s[-–]\s*([A-Z]{2,4})$/);
  if (code) return code[1];
  const short = b.tags.short_name || b.tags.alt_name;
  const name = b.name.length > 30 && short ? short : b.name;
  return name.replace(/\s+Building$/i, '');
}

export function buildingTooltip(b: Building) {
  const short = b.tags.short_name ? ` (${b.tags.short_name})` : '';
  return `${b.name}${short} · ${CATEGORY_LABEL[b.category]}`;
}

// Ranked once per building list, so layers rebuilt every animation frame keep the same data and skip re-layout.
const cache = new WeakMap<Building[], {b: Building; p: number}[]>();
const footprintCache = new WeakMap<Building[], Building[]>();
function rank(buildings: Building[]) {
  let r = cache.get(buildings);
  if (!r) cache.set(buildings, (r = buildings.filter((b) => onCampus(b.position)).map((b) => ({b, p: (KEY_CATEGORIES.includes(b.category) ? 200 : 0) + Math.min(400, Math.sqrt(area(b)) * 4)}))));
  return r;
}
function withFootprint(buildings: Building[]) {
  let r = footprintCache.get(buildings);
  if (!r) footprintCache.set(buildings, (r = buildings.filter((b) => b.footprint.length > 3)));
  return r;
}

export type Row = {b: Building; p: number; k?: number}; // k: size correction so near and far names read the same size
const sizeOf = (b: Building, focusId?: string | null) => (b.id === focusId ? 15 : KEY_CATEGORIES.includes(b.category) ? 13 : 12);

// Greedy screen-space declutter: walk buildings by priority, keep a label only if its box doesn't hit one already kept.
// Labels shrink with distance on a pitched map, so the box is scaled by how far down the screen the anchor sits.
export function declutter(rows: Row[], view: MapViewState, width: number, height: number, focusId?: string | null, left = 0): Row[] {
  const vp = new WebMercatorViewport({...view, width, height});
  const order = [...rows].sort((a, b) => (b.b.id === focusId ? 1e4 : b.p) - (a.b.id === focusId ? 1e4 : a.p));
  const kept: Row[] = [], boxes: [number, number, number, number][] = [];
  for (const r of order) {
    const z = (Number(r.b.tags.height) || 4 * (Number(r.b.tags['building:levels']) || 3)) + 4;
    const [x, y] = vp.project([r.b.position[0], r.b.position[1], z]);
    if (!(y > 70 && y < height - 20)) continue;
    const scale = 0.55 + 0.6 * (y / height);
    const size = sizeOf(r.b, focusId);
    const w = labelFor(r.b).length * size * 0.56 + 10, h = size * 1.5 + 6;
    const box: [number, number, number, number] = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
    if (box[0] < left + 8 || box[2] > width - 8) continue;
    if (boxes.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) continue;
    boxes.push(box); kept.push({...r, k: Math.min(1.5, Math.max(0.8, 1 / scale))});
  }
  return kept;
}

// Labels that fit on screen for this view. The view is rounded so a slowly orbiting camera re-runs the
// declutter about once a second rather than every frame. `canvas` is the map's container (full window if omitted).
export function useLabelRows(buildings: Building[], view: MapViewState, focusId?: string | null, canvas?: string, left = 0): Row[] {
  const key = [view.longitude.toFixed(4), view.latitude.toFixed(4), (view.zoom ?? 0).toFixed(1), Math.round((view.pitch ?? 0) / 2), Math.round((view.bearing ?? 0) / 3)].join('|');
  return useMemo(() => {
    if (!buildings.length) return [];
    const el = canvas ? document.querySelector(canvas) : null;
    const width = el?.clientWidth || window.innerWidth, height = el?.clientHeight || window.innerHeight;
    return declutter(rank(buildings), view, width, height, focusId, left);
  }, [buildings, key, focusId, canvas, left]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function buildingLabelLayers(buildings: Building[], opts: {focusId?: string | null; footprints?: boolean; rows?: Row[]} = {}): Layer[] {
  const out: Layer[] = [];
  if (opts.footprints !== false) {
    out.push(new PolygonLayer<Building>({id: 'footprints', data: withFootprint(buildings),
      getPolygon: (b) => b.footprint, stroked: true, filled: true, getFillColor: [0, 0, 0, 1], getLineColor: [150, 158, 170, 60],
      lineWidthMinPixels: 1, pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 36]}) as unknown as Layer);
  }
  const ranked = opts.rows ?? rank(buildings);
  out.push(new TextLayer<Row>({id: 'building-labels', data: ranked,
    getPosition: ({b}: Row) => [b.position[0], b.position[1], (Number(b.tags.height) || 4 * (Number(b.tags['building:levels']) || 3)) + 4] as any,
    getText: ({b}: Row) => labelFor(b),
    getSize: ({b, k}: Row) => sizeOf(b, opts.focusId) * (k ?? 1),
    getColor: ({b}: Row) => (b.id === opts.focusId ? [255, 255, 255, 255] : KEY_CATEGORIES.includes(b.category) ? [226, 228, 233, 235] : [176, 182, 192, 210]),
    sizeUnits: 'pixels', billboard: true, fontFamily: 'Geist, Inter, sans-serif', fontWeight: 500, outlineWidth: 4, outlineColor: [16, 17, 20, 235],
    fontSettings: {sdf: true, buffer: 6}, characterSet: 'auto',
    parameters: {depthCompare: 'always'} as any,
    updateTriggers: {getSize: opts.focusId, getColor: opts.focusId}} as any) as unknown as Layer);
  return out;
}
