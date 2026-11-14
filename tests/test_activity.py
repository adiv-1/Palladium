import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from apps.api.config import settings
from apps.api.video import CircularBuffer, Frame
from scripts.data.download_activity import inside, normalize

def test_geofence_follows_diagonal_not_bounding_rectangle():
    boundary=json.loads((Path(__file__).resolve().parents[1]/'docs/upc-lyft-boundary.geojson').read_text())
    ring=boundary['geometry']['coordinates'][0]
    assert inside(-118.285,34.022,ring)  # UPC
    assert inside(-118.297,34.037,ring)  # Northwest neighborhood
    assert not inside(-118.267,34.039,ring)  # Northeast bbox corner above Washington
    assert not inside(-118.266,34.013,ring)  # Southeast bbox corner east of Main
    assert not inside(-118.31,34.02,ring)

def test_normalization_keeps_case_identity_and_excludes_demographics():
    ring=[[-119,33],[-118,33],[-118,35],[-119,35],[-119,33]]
    row={'caseno':'C269044729','date_occ':'2026-10-30T23:30:00.000','date_rptd':'2026-10-31T02:00:00','time_occ':'2330','hndrdth_lat':'34.02','hndrdth_lon':'-118.28','nibr_description':'Pocket-picking theft','vict_sex':'F'}
    item,error=normalize(row,'test',True,ring)
    assert error is None
    assert item['occurred']=='2026-10-30' and item['time']=='23:30'
    assert item['case_number']=='C269044729' and 'vict_sex' not in item
    assert normalize({**row,'hndrdth_lat':'0'},'test',True,ring)[1]=='outside_boundary'

def test_history_api_date_boundaries_and_auth(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    folder=tmp_path/'activity';folder.mkdir()
    with sqlite3.connect(folder/'activity.sqlite') as db:
        db.execute('CREATE TABLE incidents (id TEXT,occurred TEXT,lon REAL,lat REAL,category TEXT,offenses TEXT,sources TEXT)')
        for day in ['2020-01-01','2020-01-02','2020-01-03']:
            db.execute('INSERT INTO incidents VALUES(?,?,?, ?,?,?,?)',(day,day,-118.28,34.02,'Other reported activity','[]','[]'))
    client=TestClient(app)
    assert client.get('/api/activity/points?start=2020-01-01&end=2020-01-02').status_code==401
    auth={'Authorization':'Bearer test-workspace-token'}
    response=client.get('/api/activity/points?start=2020-01-01&end=2020-01-02',headers=auth)
    assert response.status_code==200 and response.json()['count']==2
    assert client.get('/api/activity/points?start=2020-01-03&end=2020-01-01',headers=auth).status_code==422
    assert client.get('/api/activity/points?start=oops&end=2020-01-01',headers=auth).status_code==422
    assert client.get('/api/activity/points?start=2021-01-01&end=2021-01-02',headers=auth).json()['count']==0

def test_nonfinite_capture_timestamps_rejected():
    for stamp in [float('nan'),float('inf'),float('-inf')]:
        with pytest.raises(ValueError):CircularBuffer().append(Frame(stamp,0,b'jpeg'))
