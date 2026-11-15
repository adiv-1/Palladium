"""Build and backtest an entirely precomputed campus activity exploration bundle."""
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np

from apps.api.activity_forecast import fit, evaluate, records, distribution, MODEL_VERSION
from apps.api.activity_geo import grid, line_parts
from apps.api.activity_taxonomy import FAMILIES
from apps.api.activity_planning import planning_bundle
from apps.api.video import digest


def network(folder, boundary, cells):
    path = folder/'forecast-streets.json'
    if not path.is_file():
        raise ValueError('Download the full-zone street snapshot first: python -m scripts.data.download_forecast_streets')
    provenance = json.loads((folder/'forecast-streets-provenance.json').read_text())
    if digest(path) != provenance['sha256']:
        raise ValueError('Street snapshot hash mismatch')
    lookup = {c['id']: i for i, c in enumerate(cells)}
    ring = boundary['geometry']['coordinates'][0]
    nodes, node_index, edges, streets = [], {}, [], []
    names, name_index = [], {}
    def node(point, identity):
        # Preserve OSM topology: coincident bridges/underpasses are not connected
        # merely because their 2D coordinates happen to match.
        key = identity
        if key not in node_index:
            node_index[key] = len(nodes)
            nodes.append([round(v, 7) for v in point])
        return node_index[key]
    for way in json.loads(path.read_text())['elements']:
        tags = way.get('tags', {})
        highway = tags.get('highway')
        if highway not in {'footway', 'pedestrian', 'path', 'steps', 'living_street', 'residential',
            'unclassified', 'tertiary', 'tertiary_link', 'secondary', 'secondary_link', 'primary', 'primary_link', 'service', 'track', 'cycleway'}:
            continue
        if tags.get('foot') in {'no', 'private', 'use_sidepath'} or (tags.get('access') in {'no', 'private', 'customers', 'destination'} and tags.get('foot') not in {'yes', 'designated', 'permissive'}):
            continue
        if highway == 'cycleway' and tags.get('foot') not in {'yes', 'designated', 'permissive'}:
            continue
        if highway in {'primary', 'secondary', 'primary_link', 'secondary_link'} and tags.get('sidewalk') == 'no':
            continue
        if tags.get('indoor') == 'yes' or tags.get('area') == 'yes':
            continue
        geometry = way.get('geometry', [])
        name = tags.get('name', 'Campus path' if highway in {'footway', 'pedestrian', 'path'} else 'Unnamed walking connection')
        if name not in name_index:
            name_index[name] = len(names)
            names.append(name)
        name_id = name_index[name]
        for segment, (a, b) in enumerate(zip(geometry, geometry[1:])):
            if not a or not b:
                continue
            for first, last, cell, length in line_parts([a['lon'], a['lat']], [b['lon'], b['lat']], ring):
                if cell not in lookup or length < .1:
                    continue
                def identity(point):
                    for offset, original in enumerate((a, b)):
                        if abs(point[0]-original['lon'])+abs(point[1]-original['lat']) < 1e-9:
                            return ('osm', way['nodes'][segment+offset])
                    return ('split', way['id'], segment, round(point[0], 9), round(point[1], 9))
                u, v, ci = node(first, identity(first)), node(last, identity(last)), lookup[cell]
                edge = [u, v, round(length, 2), ci, name_id]
                if u != v:
                    edges.append(edge)
    # Keep all components for visualization; routing never invents connections.
    adjacency = [[] for _ in nodes]
    for u, v, *_ in edges:
        adjacency[u].append(v)
        adjacency[v].append(u)
    components, seen = [], set()
    for n in range(len(nodes)):
        if n in seen:
            continue
        pending, component = [n], []
        seen.add(n)
        while pending:
            u = pending.pop()
            component.append(u)
            for v in adjacency[u]:
                if v not in seen:
                    pending.append(v)
                    seen.add(v)
        components.append(component)
    largest = max(components, key=len, default=[])
    grouped = {}
    for u, v, length, ci, name_id in edges:
        grouped.setdefault((ci, name_id), []).append([nodes[u], nodes[v]])
    for (ci, name_id), coordinates in grouped.items():
        streets.append({'type': 'Feature', 'geometry': {'type': 'MultiLineString', 'coordinates': coordinates},
            'properties': {'cell': ci, 'name': names[name_id]}})
    return {'nodes': nodes, 'edges': edges, 'names': names, 'largest_component': largest,
        'coverage': {'components': len(components), 'largest_component_nodes': len(largest), 'nodes': len(nodes),
            'cells_with_connections': len({e[3] for e in edges}), 'total_cells': len(cells)},
        'provenance': provenance, 'notice': 'OSM walking connections; live closures, gate hours, lighting and crossing conditions are unverified.'}, streets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/activity'))
    args = parser.parse_args()
    folder = args.root
    metadata = json.loads((folder/'metadata.json').read_text())
    if digest(folder/'activity.sqlite') != metadata['sqlite_sha256']:
        raise ValueError('Activity snapshot hash changed')
    boundary = metadata['boundary']
    cells = grid(boundary)
    with sqlite3.connect(f'file:{(folder/"activity.sqlite").resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        items = records(db)
        history = [list(r) for r in db.execute('SELECT id,occurred,lon,lat,category,time,reported,case_number,offenses,sources FROM incidents ORDER BY occurred,id')]
    candidates = [
        {'name': 'Static spatial baseline', 'half_life': 730, 'pooling': 40, 'seasonal': False},
        {'name': 'Recent seasonal', 'half_life': 365, 'pooling': 40},
        {'name': 'Pooled seasonal, two-year half-life', 'half_life': 730, 'pooling': 40},
        {'name': 'Modern-regime seasonal', 'half_life': 365, 'pooling': 40, 'modern': True},
    ]
    validation_dates = ['2025-09-01', '2026-01-01', '2026-04-01']
    final_test_dates = ['2026-08-01', '2026-09-01']
    results = []
    for config in candidates:
        folds = []
        for cutoff in validation_dates:
            day = date.fromisoformat(cutoff)
            model = fit(items, cells, day, **{k: v for k, v in config.items() if k != 'name'})
            folds.append({'cutoff': cutoff, **evaluate(model, items, day+timedelta(days=1))})
        score = sum(f['mean_negative_log_score'] for f in folds)/len(folds)
        results.append({'config': config, 'validation_mean_negative_log_score': score, 'validation_folds': folds})
        print(config['name'], 'validation log score', round(score, 5), flush=True)
    winner = min(results[1:], key=lambda r: r['validation_mean_negative_log_score'])
    # Keep a baseline comparator on untouched final periods; these never select hyperparameters.
    for entry in (results[0], winner):
        entry['final_test_folds'] = []
        for cutoff in final_test_dates:
            day = date.fromisoformat(cutoff)
            model = fit(items, cells, day, **{k: v for k, v in entry['config'].items() if k != 'name'})
            entry['final_test_folds'].append({'cutoff': cutoff, **evaluate(model, items, day+timedelta(days=1))})
    # Select even the static baseline if its validation score is better.
    selected = min(results, key=lambda r: r['validation_mean_negative_log_score'])
    cutoff = date.fromisoformat(metadata['downloaded_at'][:10])
    model = fit(items, cells, cutoff, **{k: v for k, v in selected['config'].items() if k != 'name'})
    weekly, thresholds = {}, {}
    for k, family in enumerate(FAMILIES):
        mu = np.array(model['means'][k])
        density = mu/np.array([c['area_m2']/1e6 for c in cells])[:, None, None]
        # Fixed reference across all weekday/windows, weighted by actual cell area.
        values = density.ravel()
        weights = np.repeat([c['area_m2'] for c in cells], 56)
        order = np.argsort(values)
        cumulative = np.cumsum(weights[order])/sum(weights)
        medium, high = np.interp([.65, .9], cumulative, values[order])
        thresholds[family] = {'medium': float(medium), 'high': float(high)}
        slots = []
        for d in range(7):
            for h in range(8):
                rows = []
                for i in range(len(cells)):
                    dist = distribution(float(mu[i, d, h]), model['shapes'][k][i][d][h])
                    rows.append([dist['expected_reports'], dist['probability_at_least_one'], *dist['prediction_interval_80'], round(float(density[i, d, h]), 3)])
                slots.append(rows)
        weekly[family] = slots
    graph, streets = network(folder, boundary, cells)
    evaluation = {'kind': 'retrospective rolling-origin evaluation on a revised snapshot',
        'selected': selected['config']['name'], 'selection_metric': 'validation mean negative predictive log score; lower is better',
        'candidates': results, 'limitations': ['Publication availability and historical revisions cannot be reconstructed.',
            '80% predictive intervals are conditional empirical-Bayes approximations, not fully Bayesian calibrated guarantees.',
            'Report-family counts overlap; do not sum across families.', 'No pedestrian exposure or validated personal risk rate.']}
    artifact = {'version': MODEL_VERSION, 'scope': boundary['properties']['name'], 'boundary': boundary,
        'data_through': metadata['date_max'], 'built_from_download': metadata['downloaded_at'],
        'database_sha256': metadata['sqlite_sha256'], 'model': model, 'evaluation': evaluation,
        'density_thresholds': thresholds}
    artifact['model_id'] = hashlib.sha256(json.dumps(artifact, sort_keys=True).encode()).hexdigest()[:16]
    (folder/'forecast-model.json').write_text(json.dumps(artifact, separators=(',', ':')))
    compact_model = {k: v for k, v in model.items() if k not in {'means', 'shapes', 'cells'}}
    bundle = {k: v for k, v in artifact.items() if k != 'model'}
    pedestrian_path = folder/'pedestrian-evidence.json'
    pedestrian = json.loads(pedestrian_path.read_text()) if pedestrian_path.is_file() else {
        'status': 'not_prepared', 'routing_enabled': False, 'observations': [],
        'notice': 'No local pedestrian evidence has been prepared. Footfall is excluded from route costs.'}
    staffing = planning_bundle(graph, cells, weekly)
    bundle.update(metadata=metadata, model=compact_model, cells=cells, weekly=weekly, families=FAMILIES, graph=graph, staffing=staffing,
        streets={'type': 'FeatureCollection', 'features': streets}, history={'columns': ['id', 'date', 'lon', 'lat', 'category', 'time', 'reported', 'case_number', 'offenses_json', 'sources_json'], 'points': history},
        pedestrian=pedestrian)
    target = folder/'forecast-bundle.json'
    temporary = target.with_suffix('.building.json')
    temporary.write_text(json.dumps(bundle, separators=(',', ':'), allow_nan=False))
    temporary.replace(target)
    summary = {'model_id': artifact['model_id'], 'version': MODEL_VERSION, 'data_through': metadata['date_max'],
        'training_end': model['training_end'], 'selected': evaluation['selected'], 'cells': len(cells),
        'area_km2': sum(c['area_m2'] for c in cells)/1e6, 'network_coverage': graph['coverage'],
        'bundle_bytes': target.stat().st_size, 'evaluation': evaluation}
    (folder/'forecast-evaluation.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != 'evaluation'}, indent=2))


if __name__ == '__main__':
    main()
