import {useEffect, useState} from 'react';
import {buildGraph, stampHistory, type Graph, type Past} from './analysis';
import {parseBuildings} from './buildings';
import {simulatedHistory} from './sim';
import type {Building, Rack} from './types';

export type Campus = {buildings: Building[]; racks: Rack[]; past: Past[]; graph: Graph; historyReal: boolean};

const base = () => `${import.meta.env.BASE_URL}osm/`;

// Real: buildings, racks and the walking graph come from OpenStreetMap files.
// History is simulated unless public/osm/history.json (a geocoded DPS log) exists.
export function useCampus(): Campus | null {
  const [campus, setCampus] = useState<Campus | null>(null);
  useEffect(() => {
    (async () => {
      const get = (name: string) => fetch(base() + name).then((r) => r.json());
      const [r, b, w] = await Promise.all([get('racks.json'), get('buildings.json'), get('walk.json')]);
      const racks: Rack[] = r.racks.map((x: any) => ({id: x.id, position: [x.lng, x.lat], capacity: x.capacity}));
      const buildings = parseBuildings(b.buildings);
      let points = simulatedHistory(buildings, racks), historyReal = false;
      try {
        const h = await fetch(base() + 'history.json');
        if (h.ok && (h.headers.get('content-type') || '').includes('json')) {
          points = (await h.json()).map((p: any) => ({position: [p.lng, p.lat], hour: p.hour, type: p.type}));
          historyReal = true;
        }
      } catch { /* keep simulated history */ }
      setCampus({buildings, racks, past: stampHistory(points, buildings), graph: buildGraph(w), historyReal});
    })();
  }, []);
  return campus;
}
