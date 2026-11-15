import type {StyleSpecification} from 'maplibre-gl';

// Fallback basemap: free OpenFreeMap vector tiles (OpenMapTiles schema), no API key.
// Kept grey and dark on purpose so the only colour on screen comes from live events.
export const darkCampusStyle: StyleSpecification = {
  version: 8,
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: {
    omt: {type: 'vector', url: 'https://tiles.openfreemap.org/planet',
      attribution: '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/" target="_blank">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'},
  },
  layers: [
    {id: 'background', type: 'background', paint: {'background-color': '#121417'}},
    {id: 'landuse', type: 'fill', source: 'omt', 'source-layer': 'landuse', paint: {'fill-color': '#16191d'}},
    {id: 'park', type: 'fill', source: 'omt', 'source-layer': 'park', paint: {'fill-color': '#171c1b'}},
    {id: 'landcover', type: 'fill', source: 'omt', 'source-layer': 'landcover', paint: {'fill-color': '#161b1a', 'fill-opacity': 0.8}},
    {id: 'water', type: 'fill', source: 'omt', 'source-layer': 'water', paint: {'fill-color': '#0e1316'}},
    {id: 'paths', type: 'line', source: 'omt', 'source-layer': 'transportation', filter: ['in', ['get', 'class'], ['literal', ['path', 'track']]],
      paint: {'line-color': '#24282e', 'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.6, 19, 2.2]}},
    {id: 'minor-roads', type: 'line', source: 'omt', 'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['minor', 'service', 'tertiary']]],
      paint: {'line-color': '#262a30', 'line-width': ['interpolate', ['linear'], ['zoom'], 14, 1, 19, 9]}},
    {id: 'major-roads', type: 'line', source: 'omt', 'source-layer': 'transportation',
      filter: ['in', ['get', 'class'], ['literal', ['primary', 'secondary', 'trunk', 'motorway']]],
      paint: {'line-color': '#2e3239', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 1.5, 19, 16]}},
    {id: 'buildings-3d', type: 'fill-extrusion', source: 'omt', 'source-layer': 'building', minzoom: 14,
      paint: {
        'fill-extrusion-color': ['interpolate', ['linear'], ['coalesce', ['get', 'render_height'], 6], 0, '#2b2f35', 40, '#3c4048'],
        'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 8],
        'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
        'fill-extrusion-opacity': 0.92,
      }},
    {id: 'street-names', type: 'symbol', source: 'omt', 'source-layer': 'transportation_name', minzoom: 16,
      layout: {'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 10, 'symbol-placement': 'line'},
      paint: {'text-color': '#6c727b', 'text-halo-color': '#121417', 'text-halo-width': 1.2}},
  ],
};
