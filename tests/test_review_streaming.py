import asyncio,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from apps.api.main import app
from apps.api.db import init,save,Source
from apps.api.config import settings
from apps.api.runtime import sessions,VideoSession
from apps.api.video import Frame,encode,read_video
from apps.api.trajectories import export_trajectories
from ml.objects import extract_objects
from ml.bvimamba import prepare_inputs,restore_output
from ml.clip_review import validate_brief

def frame(t,index):return Frame(t,index,encode(np.full((48,64,3),90+index,np.uint8)))

def test_preview_skips_old_frames_for_a_slow_viewer():
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect('/ws/preview?source_id=unknown'):pass
        c.post('/api/login',json={'token':'test-workspace-token'})
        source=c.post('/api/sources',json={'kind':'phone','name':'Preview queue test'}).json()
        with c.websocket_connect('/ws/capture') as camera:
            camera.send_json({'token':source['capture_url'].split('#')[1]});camera.receive_json()
            def send(index):
                camera.send_json({'type':'frame','timestamp':100+index*.1});camera.send_bytes(frame(index*.1,index).jpeg)
                assert camera.receive_json()['type']=='ack'
            send(0)
            with c.websocket_connect('/ws/preview?source_id='+source['source']['id']) as viewer:
                metadata=viewer.receive_json();assert metadata['index']==0
                assert viewer.receive_bytes()==frame(0,0).jpeg
                send(1);send(2)
                viewer.send_json({'type':'displayed','index':0})
                assert viewer.receive_json()['index']==2
                assert viewer.receive_bytes()==frame(.2,2).jpeg
                viewer.send_json({'type':'displayed','index':2})
            camera.send_json({'type':'stop'});assert camera.receive_json()['type']=='stopped'

@pytest.mark.asyncio
async def test_recording_encoding_does_not_block_camera_receipt(monkeypatch):
    init();monkeypatch.setattr(settings,'post_event_seconds',1)
    source=save(Source(name='Recording isolation',kind='phone'));session=VideoSession(source);sessions[source.id]=session
    started=asyncio.Event();release=asyncio.Event()
    async def blocked(candidate,end,frozen_frames=None):
        assert frozen_frames;started.set();await release.wait()
    monkeypatch.setattr(session,'persist',blocked)
    try:
        for i in range(12):await asyncio.wait_for(session.push(frame(i*.5,i)),1)
        await asyncio.wait_for(started.wait(),1)
        assert session.review_alert['status']=='saving_context'
        await asyncio.wait_for(session.push(frame(6,12)),1)
        assert session.count==13
    finally:release.set();await session.close()

def test_bvi_matches_official_pixel_convention_and_preserves_aspect():
    image=np.zeros((40,20,3),np.uint8);image[:,:,0]=255
    x,shape=prepare_inputs([image],64)
    assert x.shape==(5,64,64,3)
    assert np.allclose(x[2,32,32],[1,-1,-1]) # BGR unchanged; native normalized range.
    output=restore_output(x[2],shape)
    assert output.shape==image.shape
    assert np.array_equal(output,image)

def test_dense_colored_export_filters_invalid_points_and_preserves_timing(tmp_path):
    t,h,w=3,20,12;ys,xs=np.mgrid[:h,:w]
    xyz=np.stack([np.stack([xs/10,ys/10,np.ones_like(xs)*3],-1)]*t).astype(np.float32)
    xyz[1,0,0]=np.nan;observed=np.ones((t,h,w),bool)
    images=np.zeros((t,h,w,3),np.uint8);images[...,0]=220
    poses=np.repeat(np.eye(4)[None],t,0);k=np.repeat(np.array([[10,0,6],[0,10,10],[0,0,1]])[None],t,0)
    path=tmp_path/'tracks.npz';np.savez(path,xyz=xyz,observed=observed,images=images,poses=poses,intrinsics=k)
    mapping=[{'index':10+i*3,'timestamp':8+i*.4} for i in range(t)]
    output=tmp_path/'tracks.json';video=tmp_path/'motion.mp4'
    export_trajectories(path,output,mapping,video)
    data=json.loads(output.read_text())
    assert data['version']==2 and len(data['points'][0])==240
    assert data['colors'][0]==[220,0,0]
    assert data['confidence'][1][0]==0
    assert 'NaN' not in output.read_text()
    assert data['source_frames']==mapping
    assert [round(f.timestamp,1) for f in read_video(video)]==[0,.4,.8]

def test_moss_boxes_require_real_source_alignment_and_valid_schema():
    source=frame(3,10);known={10:(source,48)}
    valid='{"frame":10,"objects":[["person",0,500,1000,1000]],"summary":"A person stands"}'
    sample=extract_objects(valid,known)[0]
    assert sample['jpeg']==source.jpeg and sample['timestamp']==3
    assert extract_objects(valid.replace('"frame":10','"frame":999'),known)==[]
    assert extract_objects(valid.replace('"person"','"criminal"'),known)==[]
    assert extract_objects(valid.replace('500,1000','1500,1000'),known)==[]
    assert extract_objects(valid.replace('"frame":10','"frame":10,"frame":10'),known)==[]

def test_separate_clip_review_rejects_unsupplied_evidence():
    frames=[frame(0,0),frame(1,1)]
    report={'takeaway':'A person raises their arms.','summary':'A person is visible in an outdoor scene.',
        'alert_support':'not_applicable','findings':[{'timestamp_start':0,'timestamp_end':1,
        'observation':'A person raises their arms.','supporting_frames':[0,1]}],
        'missing_context':['The sampled frames do not establish the surrounding context.'],
        'recommendation':'review_original','recommendation_reason':'Watch the full movement in the original clip.'}
    assert validate_brief(json.dumps(report),frames).recommendation=='review_original'
    report['findings'][0].pop('timestamp_start');report['findings'][0].pop('timestamp_end')
    assert validate_brief(json.dumps(report),frames).findings[0].timestamp_end==1
    report['summary']='A child raises their arms.'
    with pytest.raises(ValueError):validate_brief(json.dumps(report),frames)
    report['summary']='A person raises their arms.'
    report['findings'][0]['supporting_frames']=[99]
    with pytest.raises(ValueError):validate_brief(json.dumps(report),frames)
    report['findings'][0]['supporting_frames']=[0];report['recommendation']='flag_for_follow_up'
    with pytest.raises(ValueError):validate_brief(json.dumps(report),frames)

@pytest.mark.asyncio
async def test_moss_chat_uses_actual_native_responses_not_fixtures():
    from ml.adapters import MossDetector
    class Socket:
        def __aiter__(self):
            async def generate():
                for text in ['<|response|>A man ','raises an arm.<|silence|>',
                             '<|response|>A person commits theft.<|silence|>',
                             '<|response|>A person walks with',
                             '<|round_start|><|response|>A person stands.<|silence|>']:
                    yield json.dumps({'type':'output','text':text})
            return generate()
    detector=MossDetector('ws://unused');detector.use_verifier=True;detector.spatial_enabled=False;detector.ws=Socket()
    await detector.receive()
    assert detector.observer_notes[0]['text']=='A person raises an arm.'
    assert detector.observer_notes[0]['normalizations']==['neutral_person_terms']
    assert len(detector.observer_notes)==2
    assert detector.observer_notes[1]['text']=='A person stands.'
    assert detector.object_sample is None
