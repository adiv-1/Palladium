// Building facts come only from OpenStreetMap tags (public/data/buildings.json).
// Nothing safety-critical is invented: if OSM has no entrances, AEDs or access notes, none are shown.
import {distance} from './sim';
import type {Building, BuildingCategory, LngLat} from './types';

export function categorize(name: string, tags: Record<string, string>): BuildingCategory {
  const n = name.toLowerCase();
  const b = tags.building || '';
  if (tags.amenity === 'library' || n.includes('library')) return 'library';
  if (b === 'dormitory' || b === 'apartments' || n.includes('residential college') || n.includes('residence') || n.includes('apartments')) return 'residence';
  if (tags.leisure || /recreation|stadium|arena|athletic|pool|fitness|gym|field|pavilion/.test(n)) return 'recreation';
  if (b === 'parking' || tags.amenity === 'parking' || n.includes('parking')) return 'parking';
  if (/health|medical|clinic|pharmacy|hospital/.test(n)) return 'health';
  if (/dining|cafe|café|restaurant|kitchen|eatery/.test(n) || tags.amenity === 'restaurant' || tags.amenity === 'cafe') return 'dining';
  if (b === 'university' || b === 'school' || b === 'college') return 'academic';
  return 'other';
}

export const CATEGORY_LABEL: Record<BuildingCategory, string> = {
  library: 'Library', residence: 'Residence', recreation: 'Recreation and athletics', parking: 'Parking',
  health: 'Health', dining: 'Dining', academic: 'Academic', other: 'Building',
};

// Categories worth a label on the map even when nothing is happening there.
export const KEY_CATEGORIES: BuildingCategory[] = ['library', 'residence', 'recreation', 'parking', 'health'];

export function parseBuildings(raw: any[]): Building[] {
  return raw.map((b) => ({
    id: b.id, name: b.name, position: [b.lng, b.lat] as LngLat,
    footprint: (b.footprint || []) as LngLat[], tags: b.tags || {}, category: categorize(b.name, b.tags || {}),
  }));
}

function inside([x, y]: LngLat, ring: LngLat[]) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

// The building a point sits in, or the closest one within `within` metres.
export function buildingAt(buildings: Building[], point: LngLat, within = 45): Building | undefined {
  const containing = buildings.find((b) => b.footprint.length > 3 && inside(point, b.footprint));
  if (containing) return containing;
  let best: Building | undefined, bestD = within;
  for (const b of buildings) {
    const d = distance(b.position, point);
    if (d < bestD) [best, bestD] = [b, d];
  }
  return best;
}

export type Fact = {label: string; value: string; href?: string};

export function buildingFacts(b: Building): Fact[] {
  const t = b.tags;
  const facts: Fact[] = [{label: 'Type', value: CATEGORY_LABEL[b.category]}];
  const address = [t['addr:housenumber'], t['addr:street']].filter(Boolean).join(' ');
  if (address) facts.push({label: 'Address', value: address});
  if (t['building:levels']) {
    const below = t['building:levels:underground'];
    facts.push({label: 'Floors', value: `${t['building:levels']}${below ? ` (+${below} below ground)` : ''}`});
  }
  if (t.height) facts.push({label: 'Height', value: `${Math.round(Number(t.height))} m`});
  if (t.start_date) facts.push({label: 'Built', value: t.start_date});
  if (t.short_name) facts.push({label: 'Code', value: t.short_name});
  if (t.alt_name) facts.push({label: 'Also called', value: t.alt_name});
  if (t.operator) facts.push({label: 'Operator', value: t.operator});
  if (t.opening_hours) facts.push({label: 'Hours (OSM)', value: t.opening_hours});
  if (t.wheelchair) facts.push({label: 'Wheelchair', value: t.wheelchair});
  if (t.website) facts.push({label: 'Website', value: t.website.replace(/^https?:\/\//, '').replace(/\/$/, ''), href: t.website});
  if (t.wikipedia) {
    const [lang, title] = t.wikipedia.includes(':') ? t.wikipedia.split(/:(.+)/) : ['en', t.wikipedia];
    facts.push({label: 'Wikipedia', value: title, href: `https://${lang}.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, '_'))}`});
  }
  return facts;
}

export function directionsUrl(to: LngLat) {
  return `https://www.google.com/maps/dir/?api=1&destination=${to[1].toFixed(6)},${to[0].toFixed(6)}&travelmode=walking`;
}
