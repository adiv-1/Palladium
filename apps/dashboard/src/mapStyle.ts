import type {StyleSpecification} from 'maplibre-gl';
import type {Theme} from './theme';

// Basemap: free OpenFreeMap vector tiles (OpenMapTiles schema), no API key. Muted on purpose so the
// only colour on the map comes from incidents, units and alerts. One palette per theme.
type Palette = {bg: string; landuse: string; park: string; landcover: string; water: string; path: string; minor: string; major: string; bldLow: string; bldHigh: string; label: string; halo: string};

const PALETTES: Record<Theme, Palette> = {
  dark: {bg: '#121417', landuse: '#16191d', park: '#171c1b', landcover: '#161b1a', water: '#0e1316', path: '#24282e', minor: '#262a30', major: '#2e3239', bldLow: '#2b2f35', bldHigh: '#3c4048', label: '#6c727b', halo: '#121417'},
  light: {bg: '#e8e8ea', landuse: '#e2e2e4', park: '#dce3d9', landcover: '#dfe5dc', water: '#d2dade', path: '#d0d0d4', minor: '#f7f7f8', major: '#ffffff', bldLow: '#dcdcdf', bldHigh: '#c4c4c9', label: '#7a7f87', halo: '#e8e8ea'},
};

function makeStyle(p: Palette): StyleSpecification {
  return {
    version: 8,
    glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources: {
      omt: {type: 'vector', url: 'https://tiles.openfreemap.org/planet',
        attribution: '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'},
    },
    layers: [
      {id: 'background', type: 'background', paint: {'background-color': p.bg}},
      {id: 'landuse', type: 'fill', source: 'omt', 'source-layer': 'landuse', paint: {'fill-color': p.landuse}},
      {id: 'park', type: 'fill', source: 'omt', 'source-layer': 'park', paint: {'fill-color': p.park}},
      {id: 'landcover', type: 'fill', source: 'omt', 'source-layer': 'landcover', paint: {'fill-color': p.landcover, 'fill-opacity': 0.8}},
      {id: 'water', type: 'fill', source: 'omt', 'source-layer': 'water', paint: {'fill-color': p.water}},
      {id: 'paths', type: 'line', source: 'omt', 'source-layer': 'transportation', filter: ['in', ['get', 'class'], ['literal', ['path', 'track']]],
        paint: {'line-color': p.path, 'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.6, 19, 2.2]}},
      {id: 'minor-roads', type: 'line', source: 'omt', 'source-layer': 'transportation',
        filter: ['in', ['get', 'class'], ['literal', ['minor', 'service', 'tertiary']]],
        paint: {'line-color': p.minor, 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1, 19, 9]}},
      {id: 'major-roads', type: 'line', source: 'omt', 'source-layer': 'transportation',
        filter: ['in', ['get', 'class'], ['literal', ['primary', 'secondary', 'trunk', 'motorway']]],
        paint: {'line-color': p.major, 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 1.5, 19, 16]}},
      {id: 'buildings-3d', type: 'fill-extrusion', source: 'omt', 'source-layer': 'building', minzoom: 14,
        paint: {
          'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], 6], 0, p.bldLow, 40, p.bldHigh],
          'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 8],
          'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
          'fill-extrusion-opacity': 0.94,
        }},
      {id: 'street-names', type: 'symbol', source: 'omt', 'source-layer': 'transportation_name', minzoom: 16,
        layout: {'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 10, 'symbol-placement': 'line'},
        paint: {'text-color': p.label, 'text-halo-color': p.halo, 'text-halo-width': 1.2}},
    ],
  };
}

const STYLES: Record<Theme, StyleSpecification> = {dark: makeStyle(PALETTES.dark), light: makeStyle(PALETTES.light)};
export const campusStyle = (theme: Theme) => STYLES[theme];
export const darkCampusStyle = STYLES.dark; // kept for older imports
