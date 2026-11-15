"""Download real OpenStreetMap context for the dashboard: bike racks and named buildings near USC.

Run from apps/dashboard:  python scripts/build_osm_data.py
Writes public/data/racks.json and public/data/buildings.json. Data (c) OpenStreetMap contributors, ODbL.
Camera positions and officers are NOT taken from any real source; they are simulated in src/sim.ts.
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BBOX = (34.0150, -118.2935, 34.0285, -118.2770)  # south, west, north, east: UPC and surroundings
OVERPASS = 'https://overpass-api.de/api/interpreter'
OUT = Path(__file__).resolve().parent.parent / 'public' / 'data'


def query(body):
    data = urllib.parse.urlencode({'data': f'[out:json][timeout:30];({body});out center tags;'}).encode()
    request = urllib.request.Request(OVERPASS, data=data, headers={'User-Agent': 'safesc-dashboard-data-script'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)['elements']


def position(element):
    if element['type'] == 'node':
        return element['lat'], element['lon']
    return element['center']['lat'], element['center']['lon']


def main():
    south, west, north, east = BBOX
    box = f'({south},{west},{north},{east})'
    OUT.mkdir(parents=True, exist_ok=True)
    fetched = time.strftime('%Y-%m-%d')

    racks = []
    for element in query(f'node["amenity"="bicycle_parking"]{box};way["amenity"="bicycle_parking"]{box};'):
        lat, lng = position(element)
        tags = element.get('tags', {})
        racks.append({'id': f'osm-{element["type"]}-{element["id"]}', 'lat': round(lat, 6), 'lng': round(lng, 6),
                      'capacity': int(tags['capacity']) if tags.get('capacity', '').isdigit() else None,
                      'covered': tags.get('covered') == 'yes'})
    (OUT / 'racks.json').write_text(json.dumps({'source': 'OpenStreetMap amenity=bicycle_parking',
        'attribution': '(c) OpenStreetMap contributors, ODbL', 'fetched': fetched, 'racks': racks}, indent=1))

    buildings = []
    for element in query(f'way["building"]["name"]{box};relation["building"]["name"]{box};'):
        lat, lng = position(element)
        buildings.append({'id': f'osm-{element["type"]}-{element["id"]}', 'name': element['tags']['name'],
                          'lat': round(lat, 6), 'lng': round(lng, 6)})
    buildings.sort(key=lambda b: b['name'])
    (OUT / 'buildings.json').write_text(json.dumps({'source': 'OpenStreetMap named buildings',
        'attribution': '(c) OpenStreetMap contributors, ODbL', 'fetched': fetched, 'buildings': buildings}, indent=1))
    print(f'{len(racks)} racks, {len(buildings)} named buildings -> {OUT}')


if __name__ == '__main__':
    main()
