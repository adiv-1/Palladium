"""Read-only forecasting readiness audit of the local public activity snapshot.

Writes aggregate findings only; does not refresh sources, fit a forecast, or
modify the application database. All calendar fields retain Los Angeles time.
"""
import argparse
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import statistics
import xml.etree.ElementTree as ET

from apps.api.video import digest


def quantiles(values):
    values = sorted(values)
    return {str(p): values[round((len(values) - 1) * p / 100)]
            for p in (50, 90, 95, 99)} if values else {}


def audit(root):
    metadata = json.loads((root / 'metadata.json').read_text())
    with sqlite3.connect(f'file:{(root / "activity.sqlite").resolve()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM incidents')]
    end = date.fromisoformat(metadata['date_max'])
    start = date(end.year - 10, end.month, end.day) + timedelta(days=1)
    recent_start = end - timedelta(days=364)
    selected = [r for r in rows if start.isoformat() <= r['occurred'] <= end.isoformat()]
    recent = [r for r in selected if r['occurred'] >= recent_start.isoformat()]

    def summarize(items):
        delays, missing_reported = [], 0
        for r in items:
            try:
                delays.append((date.fromisoformat(r['reported']) - date.fromisoformat(r['occurred'])).days)
            except (ValueError, TypeError):
                missing_reported += 1
        weekdays = Counter(date.fromisoformat(r['occurred']).weekday() for r in items)
        return {
            'reports': len(items),
            'distinct_coordinates': len({(r['lat'], r['lon']) for r in items}),
            'systems': dict(Counter(r['system'] for r in items)),
            'categories': dict(Counter(r['category'] for r in items)),
            'hour_counts': {str(h): sum(bool(r['time']) and int(r['time'][:2]) == h for r in items) for h in range(24)},
            'weekday_counts_monday_zero': {str(d): weekdays[d] for d in range(7)},
            'missing_time': sum(not r['time'] for r in items),
            'midnight_or_0001': sum(r['time'] in ('00:00', '00:01') for r in items),
            'on_the_hour': sum(bool(r['time']) and r['time'].endswith(':00') for r in items),
            'missing_reported_date': missing_reported,
            'report_delay_days_percentiles': quantiles(delays),
            'reported_after_30_days': sum(d > 30 for d in delays),
            'multiple_offense_labels': sum(len(json.loads(r['offenses'])) > 1 for r in items),
            'identity_theft_in_theft_burglary_bucket': sum(
                r['category'] == 'Theft or burglary' and any('IDENTITY' in s.upper() for s in json.loads(r['offenses']))
                for r in items),
        }

    monthly = Counter((r['occurred'][:7], r['system']) for r in selected)
    days = (end - start).days + 1
    weekday_days = Counter((start + timedelta(days=i)).weekday() for i in range(days))
    daily = Counter(r['occurred'] for r in recent)
    daily_values = [daily[(recent_start + timedelta(days=i)).isoformat()] for i in range(365)]
    osm_nodes, osm_ways = set(), {}
    osm_files = []
    for path in sorted(root.glob('streets-*.osm')):
        document = ET.parse(path).getroot()
        osm_nodes.update(n.attrib['id'] for n in document.findall('node'))
        for way in document.findall('way'):
            tags = {t.attrib['k']: t.attrib['v'] for t in way.findall('tag')}
            osm_ways[way.attrib['id']] = tags
        osm_files.append({'file': path.name, 'bytes': path.stat().st_size})
    return {
        'kind': 'descriptive forecasting readiness audit; no fitted predictions',
        'database_sha256': digest(root / 'activity.sqlite'),
        'matches_metadata_hash': digest(root / 'activity.sqlite') == metadata.get('sqlite_sha256'),
        'snapshot_downloaded_at': metadata['downloaded_at'],
        'all_history': summarize(rows),
        'ten_year_window': {'start': start.isoformat(), 'end': end.isoformat(), 'calendar_days': days,
            **summarize(selected), 'weekday_observation_days': dict(weekday_days)},
        'last_365_days': {'start': recent_start.isoformat(), 'end': end.isoformat(),
            **summarize(recent), 'daily_mean': statistics.mean(daily_values),
            'daily_sample_variance': statistics.variance(daily_values)},
        'recent_0600_to_0700': {
            'reports': sum(bool(r['time']) and r['time'][:2] == '06' for r in recent),
            'reports_with_rail_transit_trespass_label': sum(bool(r['time']) and r['time'][:2] == '06'
                and any('Trespass Rail Transit Property' in s for s in json.loads(r['offenses'])) for r in recent),
        },
        'by_system': {s: summarize([r for r in rows if r['system'] == s]) for s in sorted({r['system'] for r in rows})},
        'monthly_counts_by_system': [{'month': m, 'system': s, 'reports': n} for (m, s), n in sorted(monthly.items())],
        'existing_osm_extracts': {'files': osm_files, 'unique_nodes': len(osm_nodes), 'unique_ways': len(osm_ways),
            'named_highway_ways': sum('highway' in t and 'name' in t for t in osm_ways.values()),
            'highway_types': dict(Counter(t['highway'] for t in osm_ways.values() if 'highway' in t)),
            'note': 'Boundary-building extracts; walking connectivity, access restrictions, and completeness are not validated.'},
        'limitations': [
            'Counts are grouped public reports, not personal harm probabilities or unique victims.',
            'No street name or verified street-segment ID exists in the normalized incident schema.',
            'Current display categories are substring rules, not a forecasting taxonomy; inspect every offense label.',
            'A revised snapshot cannot recreate historical publication availability.',
            'Whole-hour and midnight times require sensitivity analysis; rounding is not proof of an invalid time.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('data/activity'))
    parser.add_argument('--out', type=Path, default=Path('data/activity/forecast-audit.json'))
    args = parser.parse_args()
    result = audit(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('matches_metadata_hash', 'ten_year_window', 'last_365_days', 'existing_osm_extracts')}, indent=2))


if __name__ == '__main__':
    main()
