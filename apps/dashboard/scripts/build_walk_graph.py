"""Download the walkable street network around USC from OpenStreetMap and write a compact graph.

Run from apps/dashboard:  python scripts/build_walk_graph.py
Writes public/osm/walk.json = {nodes: [[lng, lat], ...], edges: [[a, b, metres], ...]}.
Data (c) OpenStreetMap contributors, ODbL. Private/no-access ways are skipped.
"""
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

BBOX = (34.0150, -118.2935, 34.0285, -118.2770)
OUT = Path(__file__).resolve().parent.parent / 'public' / 'osm' / 'walk.json'
HIGHWAYS = 'footway|path|pedestrian|steps|living_street|residential|service|unclassified|tertiary|secondary|primary|cycleway|track'


def fetch():
    s, w, n, e = BBOX
    query = (f'[out:json][timeout:90];way["highway"~"^({HIGHWAYS})$"]["access"!~"private|no"]["foot"!="no"]({s},{w},{n},{e});'
             '(._;>;);out body qt;')
    data = urllib.parse.urlencode({'data': query}).encode()
    request = urllib.request.Request('https://overpass-api.de/api/interpreter', data=data, headers={'User-Agent': 'safesc-dashboard-data-script'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)['elements']


def metres(a, b):
    dx = (b[0] - a[0]) * 111320 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (b[1] - a[1]) * 111320
    return math.hypot(dx, dy)


def main():
    elements = fetch()
    coords = {e['id']: (e['lon'], e['lat']) for e in elements if e['type'] == 'node'}
    index, nodes, edges = {}, [], set()
    def node(osm_id):
        if osm_id not in index:
            index[osm_id] = len(nodes)
            nodes.append([round(coords[osm_id][0], 6), round(coords[osm_id][1], 6)])
        return index[osm_id]
    for way in (e for e in elements if e['type'] == 'way'):
        ids = [i for i in way['nodes'] if i in coords]
        for a, b in zip(ids, ids[1:]):
            if a != b:
                edges.add(tuple(sorted((node(a), node(b)))))
    graph_edges = [[a, b, round(metres(nodes[a], nodes[b]), 1)] for a, b in sorted(edges)]
    # Keep only the largest connected component so every node can reach every other.
    parent = list(range(len(nodes)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, _ in graph_edges:
        parent[find(a)] = find(b)
    sizes = {}
    for i in range(len(nodes)):
        sizes[find(i)] = sizes.get(find(i), 0) + 1
    root = max(sizes, key=sizes.get)
    remap, kept = {}, []
    for i, p in enumerate(nodes):
        if find(i) == root:
            remap[i] = len(kept)
            kept.append(p)
    final = [[remap[a], remap[b], d] for a, b, d in graph_edges if a in remap and b in remap]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'source': 'OpenStreetMap highway ways (walkable)', 'attribution': '(c) OpenStreetMap contributors, ODbL',
                               'nodes': kept, 'edges': final}, separators=(',', ':')))
    print(f'{len(kept)} nodes, {len(final)} edges, {OUT.stat().st_size // 1024} KB')


if __name__ == '__main__':
    main()
