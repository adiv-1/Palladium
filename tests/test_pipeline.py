import asyncio
import json
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from apps.api.config import settings
from apps.api.db import init, rows, Incident, Source, ReviewAction, save
from apps.api.video import CircularBuffer, LightingGate, Frame, encode, read_video, write_clip, digest
from apps.api.schemas import Detection
from apps.api.runtime import VideoSession, sessions
from ml.adapters import TemporalGate, MossDetector

def frame(t,index=None):
    image=np.full((120,160,3),80,np.uint8)
    return Frame(t,index if index is not None else int(t*10),encode(image))

def detection(t=2,indices=None,**overrides):
    return Detection.model_validate({'state':'REVIEW_REQUIRED','confidence':.9,'observable_events':[
        {'timestamp_start':t-1,'timestamp_end':t,'actors':['person_a'],'observation':'person_a falls after contact'}],
        'uncertainties':['Context is unclear'],'supporting_frames':indices or [int(t*10)-1,int(t*10)],**overrides})

def test_buffer_boundaries_and_nonmonotonic():
    b=CircularBuffer(2)
    for t in range(5): b.append(frame(t))
    assert [f.timestamp for f in b.window(1,3)]==[2,3]
    with pytest.raises(ValueError): b.append(frame(3))
    assert b.size==sum(len(f.jpeg) for f in b.frames)

def test_buffer_memory_cap():
    f=frame(0); b=CircularBuffer(100,len(f.jpeg)*2)
    for t in range(20): b.append(frame(t))
    assert len(b.frames)<=2

def test_lighting_hysteresis():
    gate=LightingGate()
    dark=np.zeros((64,64,3),np.uint8); bright=np.full_like(dark,150)
    for _ in range(8): gate.update(dark)
    assert gate.state=='severely_dark'
    gate.update(bright);assert gate.state=='severely_dark'
    for _ in range(8): gate.update(bright)
    assert gate.state=='well_lit'

def test_h264_timestamps_and_immutability(tmp_path):
    path=tmp_path/'original.mp4'; write_clip([frame(10),frame(10.25),frame(11)],path)
    before=digest(path)
    assert [round(f.timestamp,2) for f in read_video(path)]==[0,.25,1]
    with pytest.raises(FileExistsError): write_clip([frame(0)],path)
    assert digest(path)==before

def test_schema_rejects_malformed_legal_and_identity():
    with pytest.raises(ValueError): detection(confidence=2)
    with pytest.raises(ValueError): detection(observable_events=[{'timestamp_start':0,'timestamp_end':1,'actors':['person_a'],'observation':'The criminal commits robbery'}])
    with pytest.raises(ValueError): detection(supporting_frames=['not-a-frame'])

def test_temporal_support_cooldown_and_contradiction():
    gate=TemporalGate(cooldown=20)
    assert not gate.consider(detection(2),2,range(100))
    assert not gate.consider(detection(2),2.2,range(100))
    assert gate.consider(detection(3),3,range(100))
    assert not gate.consider(detection(4),4,range(100))
    gate=TemporalGate()
    gate.consider(detection(2),2,range(100))
    gate.consider(detection(2,state='NORMAL'),2.2,range(100))
    assert not gate.consider(detection(3),3,range(100))
    assert not TemporalGate().consider(detection(3),3,[])

@pytest.mark.asyncio
async def test_complete_fixture_pipeline(monkeypatch):
    init();monkeypatch.setattr(settings,'post_event_seconds',1)
    src=save(Source(name='Staged fixture',kind='upload'))
    session=VideoSession(src);sessions[src.id]=session
    for n in range(16): await session.push(frame(n*.5,n))
    await session.close()
    incident=next(i for i in rows(Incident) if i.source_id==src.id)
    assert incident.simulated
    path=settings.data_dir/'incidents'/incident.id/'original.mp4'
    assert digest(path)==incident.record['original_sha256']
    assert incident.start_time==0
    assert incident.end_time==incident.record['trigger_time']+1
    assert len(incident.record['frame_timestamps'])>=9

def test_authenticated_api_and_review_history():
    with TestClient(app) as c:
        assert c.get('/api/incidents').status_code==401
        assert c.post('/api/login',json={'token':'wrong'}).status_code==401
        assert c.post('/api/login',json={'token':'test-workspace-token'}).status_code==200
        assert c.get('/api/health').json()['simulated']
        incident=save(Incident(source_id='test',start_time=0,end_time=2,severity='WATCH',confidence=.8,record={}))
        for status in ['needs_review','dismissed']:
            assert c.post(f'/api/incidents/{incident.id}/review',json={'decision':status,'note':'reviewed'}).status_code==200
        r=c.get(f'/api/incidents/{incident.id}').json()
        assert len(r['history'])==2
        assert r['incident']['review_status']=='dismissed'
        assert c.get(f'/api/incidents/{incident.id}/media/not-a-real-artifact').status_code==404
        exported=c.get('/api/reviews/export').json()
        assert exported['automatic_training'] is False
        assert len([r for r in exported['reviews'] if r['incident_id']==incident.id])==2

@pytest.mark.asyncio
async def test_reviewer_bookmark_is_not_an_ai_incident_claim():
    init();source=save(Source(name='Reviewer bookmark test',kind='phone'))
    session=VideoSession(source);sessions[source.id]=session
    for index in range(3):await session.push(frame(index*.5,index))
    result=session.bookmark();assert result['status']=='marked'
    await session.close()
    incident=next(i for i in rows(Incident) if i.source_id==source.id)
    assert incident.record['trigger_origin']=='reviewer' and incident.confidence==0
    assert 'no AI detection score' in incident.record['confidence_note']
    assert digest(settings.data_dir/'incidents'/incident.id/'original.mp4')==incident.record['original_sha256']
    assert any(r.incident_id==incident.id and r.decision=='needs_review' for r in rows(ReviewAction))

def test_phone_pairing_ack_and_disconnect():
    with TestClient(app) as c:
        c.post('/api/login',json={'token':'test-workspace-token'})
        pair=c.post('/api/sources',json={'name':'Phone test','kind':'phone'}).json()
        token=pair['capture_url'].split('#')[1]
        with c.websocket_connect('/ws/capture') as ws:
            ws.send_json({'token':token}); assert ws.receive_json()['type']=='ready'
            ws.send_json({'type':'frame','timestamp':10});ws.send_bytes(frame(0).jpeg)
            ack=ws.receive_json();assert ack['type']=='ack' and ack['timestamp']==0
            ws.send_json({'type':'stop'})
            assert ws.receive_json()['type']=='stopped'
        assert pair['source']['id'] not in sessions

@pytest.mark.asyncio
async def test_moss_protocol_partial_json_and_cleanup():
    import websockets
    async def serve(ws):
        hello=json.loads(await ws.recv());assert hello['type']=='start'
        await ws.send(json.dumps({'type':'ready'}))
        meta=json.loads(await ws.recv());assert meta['timestamp']==2
        assert isinstance(await ws.recv(),bytes)
        text=detection().model_dump_json()
        for chunk in [text[:30],text[30:]]: await ws.send(json.dumps({'type':'output','text':chunk}))
        await ws.wait_closed()
    async with websockets.serve(serve,'127.0.0.1',0) as server:
        port=server.sockets[0].getsockname()[1];detector=MossDetector(f'ws://127.0.0.1:{port}')
        await detector.observe([frame(2)],[2],[])
        for _ in range(20):
            if detector.pending: break
            await asyncio.sleep(.01)
        assert len(detector.drain())==1
        await detector.close();assert detector.task.done()

@pytest.mark.asyncio
async def test_camera_intake_continues_while_gpu_is_busy(monkeypatch):
    from apps.api import runtime,jobs
    started=asyncio.Event();release=asyncio.Event()
    class SlowDetector:
        def __init__(self,url):self.calls=0
        async def observe(self,*args):
            self.calls+=1;started.set();await release.wait()
        def drain(self):return []
        async def close(self):pass
    monkeypatch.setattr(settings,'streaming_model','moss')
    monkeypatch.setattr(settings,'enable_tracking',False)
    monkeypatch.setattr(settings,'enhancement_model','bypass')
    monkeypatch.setattr(runtime,'MossDetector',SlowDetector)
    monkeypatch.setattr(jobs,'gpu_busy',True)
    src=save(Source(name='Intake isolation test',kind='phone'))
    session=VideoSession(src);sessions[src.id]=session
    try:
        for index in range(4):
            await asyncio.wait_for(session.push(frame(index*.5,index)),2)
        assert session.count==4 and session.detector.calls==0
        assert session.inference_queue.qsize()<=1
        monkeypatch.setattr(jobs,'gpu_busy',False)
        await asyncio.wait_for(started.wait(),2)
        # An in-flight slow inference also must not hold up camera receipt.
        await asyncio.wait_for(session.push(frame(2,4)),2)
        assert session.count==5
    finally:
        release.set();await session.close()

def test_dispatch_relay_forwards_within_a_room_only():
    room='A'*22
    with TestClient(app) as c:
        with c.websocket_connect(f'/ws/dispatch/{room}') as dashboard, \
             c.websocket_connect(f'/ws/dispatch/{room}') as phone, \
             c.websocket_connect(f'/ws/dispatch/{"B"*22}') as other:
            dashboard.send_text(json.dumps({'type':'assign','unit':'officer-1'}))
            assert phone.receive_json()=={'type':'assign','unit':'officer-1'}
            phone.send_text(json.dumps({'type':'ack','unit':'officer-1'}))
            assert dashboard.receive_json()['type']=='ack'
            other.send_text(json.dumps({'type':'ping'}))
            dashboard.send_text('not json')
            dashboard.send_text(json.dumps({'type':'after'}))
            assert phone.receive_json()=={'type':'after'}  # neither the other room nor invalid JSON arrived
        from starlette.websockets import WebSocketDisconnect as Closed
        with pytest.raises(Closed):
            with c.websocket_connect('/ws/dispatch/short') as bad: bad.receive_text()
