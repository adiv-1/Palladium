"""Publish/restore an explicit public-activity allowlist, never the whole data directory."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3


FILES = ('activity.sqlite', 'metadata.json', 'forecast-bundle.json', 'forecast-model.json',
    'forecast-evaluation.json', 'forecast-streets.json', 'forecast-streets-provenance.json',
    'pedestrian-evidence.json', 'forecast-audit.json')
COMPRESS = {'activity.sqlite', 'forecast-bundle.json', 'forecast-model.json', 'forecast-streets.json'}
EXPECTED_COLUMNS = {'id', 'case_number', 'occurred', 'reported', 'time', 'lat', 'lon',
    'category', 'offenses', 'sources', 'system'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pack(source, destination):
    metadata = json.loads((source/'metadata.json').read_text())
    database = (source/'activity.sqlite').read_bytes()
    if sha(database) != metadata['sqlite_sha256']:
        raise ValueError('Activity database differs from its public source manifest')
    with sqlite3.connect(f'file:{(source/"activity.sqlite").resolve()}?mode=ro', uri=True) as db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(incidents)')}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        count = db.execute('SELECT count(*) FROM incidents').fetchone()[0]
    if columns != EXPECTED_COLUMNS or tables != {'incidents'} or count != metadata['count']:
        raise ValueError('Unexpected schema: refusing to publish a different database')
    destination.mkdir(parents=True, exist_ok=True)
    entries = []
    sources = [(name, name, name in COMPRESS) for name in FILES]
    # Include every selected source page referenced by the authoritative snapshot.
    sources.extend((page['file'], 'raw/'+page['file'], False)
        for item in metadata['sources'] for page in item['pages'])
    for original, name, compress in sources:
        if Path(original).name != original:
            raise ValueError('Source manifest contains a nonlocal filename')
        raw = (source/original).read_bytes()
        stored = gzip.compress(raw, mtime=0) if compress else raw
        relative = name+('.gz' if compress else '')
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stored)
        entries.append({'file': relative, 'restore_as': original, 'gzip_encoded': compress,
            'bytes': len(stored), 'sha256': sha(stored), 'restored_bytes': len(raw), 'restored_sha256': sha(raw)})
    manifest = {'version': 1, 'kind': 'minimized public campus activity snapshot',
        'snapshot_downloaded_at': metadata['downloaded_at'], 'reports': count,
        'occurrence_start': metadata['date_min'], 'occurrence_end': metadata['date_max'],
        'scope': metadata['boundary']['properties']['name'], 'sources': [s['url'] for s in metadata['sources']],
        'contents': entries, 'notice': 'Public report history and derived research analysis, not personal safety probabilities.',
        'osm_attribution': '© OpenStreetMap contributors, ODbL. https://www.openstreetmap.org/copyright',
        'exclusions': ['Credentials', 'Private videos', 'Application/learning databases', 'Provider account records', 'Model weights']}
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print('Packaged', count, 'public reports;', len(entries), 'files;', sum(e['bytes'] for e in entries), 'compressed bytes')


def restore(source, destination):
    manifest = json.loads((source/'manifest.json').read_text())
    validated = []
    for entry in manifest['contents']:
        relative = Path(entry['file'])
        name = entry['restore_as']
        if relative.is_absolute() or '..' in relative.parts or Path(name).name != name:
            raise ValueError('Unsafe snapshot path')
        stored = (source/relative).read_bytes()
        if sha(stored) != entry['sha256']:
            raise ValueError('Snapshot checksum mismatch: '+entry['file'])
        raw = gzip.decompress(stored) if entry['gzip_encoded'] else stored
        if len(raw) != entry['restored_bytes'] or sha(raw) != entry['restored_sha256']:
            raise ValueError('Restored checksum mismatch: '+name)
        target = destination/name
        if target.exists() and sha(target.read_bytes()) != entry['restored_sha256']:
            raise ValueError('Refusing to replace different existing public data: '+name)
        validated.append((target, raw))
    destination.mkdir(parents=True, exist_ok=True)
    for target, raw in validated:
        if not target.exists():
            temporary = target.with_name(target.name+'.snapshot-tmp')
            temporary.write_bytes(raw)
            temporary.replace(target)
    print('Verified/restored', len(validated), 'public activity files to', destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['pack', 'restore'])
    parser.add_argument('--snapshot', type=Path, default=Path('public-data/activity/2026-11-14'))
    parser.add_argument('--data', type=Path, default=Path('data/activity'))
    args = parser.parse_args()
    if args.action == 'pack':
        pack(args.data, args.snapshot)
    else:
        restore(args.snapshot, args.data)


if __name__ == '__main__':
    main()
