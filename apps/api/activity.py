"""Read-only, privacy-minimized public incident history (separate from live video)."""
import json
import sqlite3
from datetime import date
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from .config import settings

router = APIRouter(prefix='/api/activity')


@router.get('/exploration-bundle')
def exploration_bundle():
    """One authenticated download; all forecast/time/route analysis is preloaded."""
    path = settings.data_dir / 'activity' / 'forecast-bundle.json'
    if not path.is_file():
        raise HTTPException(503, 'Activity outlook is not prepared. Run python -m scripts.build_activity_forecast on the data host.')
    return FileResponse(path, media_type='application/json', headers={'Cache-Control': 'private, no-cache'})

def database():
    path = settings.data_dir / 'activity' / 'activity.sqlite'
    if not path.exists():
        raise HTTPException(503, 'Public history has not been downloaded yet. Run scripts/data/download_activity.py.')
    conn = sqlite3.connect(f'file:{path.resolve()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn

@router.get('/meta')
def metadata():
    path = settings.data_dir / 'activity' / 'metadata.json'
    if not path.exists():
        raise HTTPException(503, 'Public history is not installed yet')
    return json.loads(path.read_text())

@router.get('/points')
def points(start: date = Query(...), end: date = Query(...)):
    if start > end:
        raise HTTPException(422, 'Start date must be on or before end date')
    with database() as conn:
        rows = conn.execute('SELECT id,occurred,lon,lat,category FROM incidents WHERE occurred BETWEEN ? AND ? ORDER BY occurred,id', (start.isoformat(),end.isoformat())).fetchall()
    # Compact columns keep a decade of points small; no victim fields or addresses.
    return {'columns':['id','date','lon','lat','category'], 'points':[list(row) for row in rows], 'count':len(rows)}

@router.get('/incidents/{incident_id}')
def detail(incident_id: str):
    with database() as conn:
        row = conn.execute('SELECT * FROM incidents WHERE id=?',(incident_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Record not found')
    item = dict(row)
    for key in ['offenses','sources']:
        item[key] = json.loads(item[key])
    item['summary'] = f"Public records list a report categorized as {item['category'].lower()}, with an occurrence date of {item['occurred']}. The marker is an approximate reported location."
    item['context'] = 'A public report is not a finding of guilt or a measure of your personal risk. Multiple classifications may describe one report.'
    return item
