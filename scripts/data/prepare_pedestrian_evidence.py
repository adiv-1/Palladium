"""Minimize LADOT survey counts and locate corridors using the zone's OSM snapshot.

No demographic attributes are requested or retained. Counts remain survey
totals; this script deliberately does not invent hourly pedestrian estimates.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import httpx

from apps.api.activity_geo import inside

ROOT = Path('data/activity')


def name_tokens(name):
    return set(re.sub(r'[^a-z0-9 ]', '', name.lower()).split()) - {
        'west', 'east', 'north', 'south', 'street', 'st', 'boulevard', 'blvd', 'avenue', 'ave', 'drive', 'dr'}


def main():
    fields = 'street_name,limit_1,limit_2,full_location_names_2025,ped_total,ped_weekday_total,ped_weekend'
    response = httpx.get('https://data.lacity.org/resource/hiwe-xzrt.json',
        params={'$select': fields, '$limit': 1000}, timeout=40)
    response.raise_for_status()
    rows = response.json()
    if len(rows) >= 1000:
        raise ValueError('Unexpected count dataset size; pagination needs review')
    osm = json.loads((ROOT/'forecast-streets.json').read_text())['elements']
    boundary = json.loads((ROOT/'metadata.json').read_text())['boundary']
    ring = boundary['geometry']['coordinates'][0]
    roads = []
    coords = {}
    for way in osm:
        if 'name' not in way.get('tags', {}):
            continue
        roads.append((name_tokens(way['tags']['name']), set(way['nodes'])))
        coords.update({n: [p['lon'], p['lat']] for n, p in zip(way['nodes'], way['geometry']) if p})
    def nodes_for(name):
        tokens = name_tokens(name)
        return set().union(*(nodes for words, nodes in roads if words == tokens))
    observations = []
    for row in rows:
        main_nodes = nodes_for(row['street_name'])
        ends = []
        for cross in (row['limit_1'], row['limit_2']):
            matches = [coords[n] for n in main_nodes & nodes_for(cross) if n in coords]
            if not matches:
                break
            ends.append([sum(p[i] for p in matches)/len(matches) for i in (0, 1)])
        if len(ends) != 2:
            continue
        center = [(ends[0][i]+ends[1][i])/2 for i in (0, 1)]
        if not inside(center, ring):
            continue
        observations.append({'name': row['full_location_names_2025'], 'coordinates': center,
            'corridor_endpoints': ends, 'year': 2025, 'pedestrians_sampled': int(row['ped_total']),
            'weekday_period_total': int(row['ped_weekday_total']), 'weekend_period_total': int(row['ped_weekend']),
            'source': 'https://data.lacity.org/d/hiwe-xzrt',
            'location_precision': 'Approximate corridor midpoint from OSM named-road intersections; not a verified counter location',
            'note': 'Survey-period totals; not pedestrians/day, unique people, or an hourly flow estimate.'})
    result = {'status': 'insufficient_zone_coverage', 'routing_enabled': False, 'observations': observations,
        'notice': 'Verified survey corridors do not support a full-zone hourly flow model. Footfall is not included in route costs.',
        'source': 'https://data.lacity.org/d/hiwe-xzrt', 'source_rows': len(rows), 'selected_fields': fields,
        'source_response_sha256': hashlib.sha256(response.content).hexdigest(),
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'period_metadata': 'Published survey windows: weekdays 07:00–10:00 and 15:00–18:00; weekends 11:00–13:00. Exact site dates and original hour-level records not supplied by this aggregate table.',
        'source_program': 'https://ladot.lacity.gov/walkbikecount'}
    (ROOT/'pedestrian-evidence.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
