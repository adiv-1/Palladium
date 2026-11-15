import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from apps.api.activity_forecast import distribution, fit
from apps.api.activity_geo import area, cell_id, grid, inside, line_parts, xy
from apps.api.activity_taxonomy import families
from apps.api.activity_planning import greedy_posts
from apps.api.config import settings
from apps.api.main import app
from scripts.build_activity_forecast import network
from apps.api.video import digest


def boundary():
    return json.loads((Path(__file__).resolve().parents[1]/'docs/upc-lyft-boundary.geojson').read_text())


def test_full_zone_grid_has_no_area_gaps_and_clips_diagonal():
    shape = boundary()
    cells = grid(shape)
    polygon = [xy(p) for p in shape['geometry']['coordinates'][0][:-1]]
    assert math.isclose(sum(c['area_m2'] for c in cells), area(polygon), rel_tol=1e-9)
    ids = {c['id'] for c in cells}
    # Dense coverage check over the whole polygon, not just near campus.
    for lon in np.linspace(-118.301, -118.264, 65):
        for lat in np.linspace(34.0109, 34.04, 65):
            if inside([lon, lat], shape['geometry']['coordinates'][0]):
                assert cell_id(lon, lat) in ids
    assert all(inside(c['center'], shape['geometry']['coordinates'][0]) for c in cells)


def test_line_splitting_keeps_only_in_zone_and_assigns_correct_cells():
    ring = boundary()['geometry']['coordinates'][0]
    parts = list(line_parts([-118.32, 34.03], [-118.25, 34.03], ring))
    assert len(parts) > 5
    for a, b, identity, meters in parts:
        mid = [(a[0]+b[0])/2, (a[1]+b[1])/2]
        assert inside(mid, ring) and cell_id(*mid) == identity and meters > 0


def test_walking_network_preserves_osm_topology_and_excludes_busways(tmp_path):
    def way(identity, nodes, points, highway='footway'):
        return {'id': identity, 'nodes': nodes, 'tags': {'highway': highway, 'name': str(identity)},
            'geometry': [{'lon': p[0], 'lat': p[1]} for p in points]}
    # Different OSM nodes at a coincident bridge/underpass point must stay apart.
    elements = [way(1, [1, 2, 3], [[-118.286, 34.022], [-118.285, 34.022], [-118.284, 34.022]]),
        way(2, [4, 5, 6], [[-118.285, 34.021], [-118.285, 34.022], [-118.285, 34.023]]),
        way(3, [7, 8], [[-118.287, 34.022], [-118.288, 34.022]], 'busway')]
    path = tmp_path/'forecast-streets.json'
    path.write_text(json.dumps({'elements': elements}))
    (tmp_path/'forecast-streets-provenance.json').write_text(json.dumps({'sha256': digest(path)}))
    graph, streets = network(tmp_path, boundary(), grid(boundary()))
    assert graph['coverage']['components'] == 2
    assert '3' not in graph['names']
    assert streets


def test_taxonomy_excludes_financial_and_enforcement_and_deduplicates():
    assert not families(['THEFT OF IDENTITY', '530.5(A) - PC - F - Identity Theft - 26F'])
    assert not families(['978.5 - PC - M - Bench Warrant - 90Z', 'Trespass Rail Transit Property - 90J'])
    assert families(['BATTERY - SIMPLE ASSAULT', 'ROBBERY', '243(A) - PC - M - Battery On Person - 13B']) == {'person'}
    assert families(['487(D)(1) - PC - F - Grand Theft Auto - 240']) == {'property'}
    assert families(['THEFT PLAIN - PETTY', 'ROBBERY']) == {'person', 'property'}


def test_fit_uses_report_availability_maturity_and_zero_exposure():
    cells = grid(boundary(), size=1000)
    cutoff = date(2026, 6, 1)
    def item(day, reported):
        return {'id': str(day), 'day': day, 'occurred': day.isoformat(), 'reported': reported,
            'lon': -118.285, 'lat': 34.022, 'families': ['person'], 'weekday': day.weekday(), 'slot': 6}
    good = item(cutoff-timedelta(days=40), '2026-05-01')
    late = item(cutoff-timedelta(days=40), '2026-06-02')
    immature = item(cutoff-timedelta(days=2), '2026-05-31')
    missing = item(cutoff-timedelta(days=40), '')
    model = fit([good, late, immature, missing], cells, cutoff, size=1000)
    assert model['training_reports'] == 1
    assert sum(model['support'][0]) == 1
    assert np.isfinite(np.array(model['means'])).all()
    assert np.min(model['means']) > 0  # Empty areas are pooled, not assigned zero risk.
    duplicated = fit([good, good], cells, cutoff, size=1000)
    assert np.sum(duplicated['means'][0]) > np.sum(model['means'][0])


def test_predictive_probability_and_count_intervals():
    result = distribution(.2, 2)
    assert abs(result['probability_at_least_one']-(1-(2/2.2)**2)) < 1e-5
    assert result['prediction_interval_80'][0] == 0
    assert distribution(2, 2)['prediction_interval_80'][1] > result['prediction_interval_80'][1]
    assert distribution(0, 2)['probability_at_least_one'] == 0


def test_staffing_adds_nonoverlapping_coverage_and_changes_with_time_demand():
    candidates = [{'cell': 0, 'covers': [0, 1]}, {'cell': 1, 'covers': [1]}, {'cell': 2, 'covers': [2]}]
    daytime = greedy_posts(candidates, [3, 5, 6], budget=3)
    assert [p['cell'] for p in daytime] == [0, 2]
    assert daytime[0]['incremental_expected_reports'] == 8
    assert daytime[1]['cumulative_expected_reports'] == 14
    nighttime = greedy_posts(candidates, [1, 2, 12], budget=1)
    assert nighttime[0]['cell'] == 2
    assert greedy_posts(candidates, [0, 0, 0]) == []


def test_bundle_is_authenticated_static_and_reports_missing_preparation(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    folder = tmp_path/'activity'
    folder.mkdir()
    client = TestClient(app)
    url = '/api/activity/exploration-bundle'
    assert client.get(url).status_code == 401
    auth = {'Authorization': 'Bearer test-workspace-token'}
    assert client.get(url, headers=auth).status_code == 503
    prepared = {'model_id': 'prepared', 'weekly': {'person': [[[.1, .09, 0, 1, 2]]]}, 'history': {'points': []}}
    (folder/'forecast-bundle.json').write_text(json.dumps(prepared))
    response = client.get(url, headers=auth)
    assert response.status_code == 200 and response.json() == prepared
    assert response.headers['cache-control'] == 'private, no-cache'
