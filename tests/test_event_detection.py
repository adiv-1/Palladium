import asyncio,copy,json
from collections import deque
from types import SimpleNamespace
import numpy as np
import pytest
from PIL import Image
from apps.api.config import settings
from apps.api.db import init,save,Source,Incident,rows
from apps.api.runtime import VideoSession,sessions
from apps.api.schemas import Detection
from apps.api.video import Frame,encode,digest,select_evidence
from ml.adapters import TemporalGate,MossDetector
from ml.verifier import validate_visual_output,generation_target
from scripts.augment_event_training import augment

def detection(start,end,indices,action='physical_contact'):
    return Detection(state='REVIEW_REQUIRED',confidence=.8,action=action,
        observable_events=[{'timestamp_start':start,'timestamp_end':end,'actors':[],
            'observation':'One person makes striking contact with another person.' if action=='physical_contact' else 'A person removes an item from a bag.'}],
        uncertainties=['Context requires human review.'],supporting_frames=indices)

def test_generated_source_times_are_derived_from_real_frame_ids():
    frames=[Frame(501+i*.5,800+i*7,b'') for i in range(4)]
    value=generation_target(detection(501,502.5,[800,821]).model_dump())
    for event in value['observable_events']:
        event.pop('timestamp_start');event.pop('timestamp_end')
    parsed=validate_visual_output(json.dumps(value),frames)
    assert parsed.observable_events[0].timestamp_start==501
    assert parsed.observable_events[0].timestamp_end==502.5
    value['observable_events'][0].update(timestamp_start=1,timestamp_end=83)
    assert validate_visual_output(json.dumps(value),frames).observable_events[0].timestamp_start==501
    value['supporting_frames']=[999]
    with pytest.raises(ValueError):validate_visual_output(json.dumps(value),frames)

def test_motion_sampling_retains_brief_change_and_real_context():
    frames=[Frame(i*.1,i,b'',motion=100 if i==43 else 0) for i in range(100)]
    chosen=select_evidence(frames,8)
    assert len(chosen)==8 and chosen[0].index==0 and chosen[-1].index==99
    assert any(f.index==43 for f in chosen)
    assert any(3.9<=f.timestamp<=4.1 for f in chosen)
    assert any(4.5<=f.timestamp<=4.7 for f in chosen)
    assert [f.timestamp for f in chosen]==sorted(f.timestamp for f in chosen)
    short=select_evidence(frames,4)
    assert len(short)==4 and 43 in [f.index for f in short]

def test_event_time_agreement_survives_slow_inference_and_separates_actions():
    a=detection(1,3,[1,2]);b=detection(2,4,[3,4])
    gate=TemporalGate(agreement_window=6)
    assert not gate.consider(a,20,range(10))
    assert gate.consider(b,40,range(10)) # same short event, different wall-clock completion times
    mixed=TemporalGate(agreement_window=6)
    assert not mixed.consider(a,20,range(10))
    assert not mixed.consider(detection(2,4,[3,4],'object_taking'),21,range(10))

def test_repeated_identical_real_image_cannot_create_an_incident():
    init();source=save(Source(name='Static-frame evidence check',kind='phone'));session=VideoSession(source)
    jpeg=encode(np.full((48,64,3),100,np.uint8))
    for i in range(11):session.buffer.append(Frame(i*.5,i,jpeg))
    session.detector.pending.extend([detection(3,4,[6,8]),detection(4,5,[8,10])])
    session.collect_detections(6)
    assert session.review_alert is None and not session.pending
    assert session.gate.last_reason=='duplicate_visual_evidence'

@pytest.mark.asyncio
async def test_native_work_yields_while_action_verifier_is_running():
    class Socket:
        async def send(self,_):raise AssertionError('Native model must not receive more GPU work during verification')
    detector=MossDetector('ws://unused');detector.use_verifier=True;detector.ws=Socket()
    detector.task=SimpleNamespace(done=lambda:False)
    detector.verification_task=asyncio.get_running_loop().create_future()
    try:await detector.observe([],[],[])
    finally:detector.verification_task.cancel()

@pytest.mark.asyncio
async def test_event_confirmation_uses_other_original_support_frames():
    class Verifier:
        last_audit={}
        def __init__(self):self.calls=[]
        def verify_frames(self,frames,notes,tracks):
            self.calls.append([f.index for f in frames])
            ids=[0,2,4,6] if len(self.calls)==1 else [f.index for f in frames]
            return detection(min(f.timestamp for f in frames),max(f.timestamp for f in frames),ids)
    detector=MossDetector('ws://unused');detector.verifier=Verifier()
    async def prepared():pass
    detector.prepare_verifier=prepared
    frames=[Frame(i*.5,i,b'') for i in range(12)]
    await detector.verify_window(frames[:8],'untrusted first caption',[],frames)
    outputs=detector.drain()
    assert len(outputs)==2
    assert not set(outputs[0].supporting_frames)&set(outputs[1].supporting_frames)
    assert detector.response_audit[-1]['pass']=='event_context_confirmation'

@pytest.mark.asyncio
@pytest.mark.parametrize('action,expected_end',[('physical_contact',6),('object_taking',8)])
async def test_delayed_event_preserves_original_and_saved_chat_notice(monkeypatch,action,expected_end):
    init();monkeypatch.setattr(settings,'post_event_seconds',1)
    monkeypatch.setattr(settings,'object_post_event_seconds',3)
    class Quiet:
        def __init__(self):self.pending=deque()
        async def observe(self,*args):pass
        def drain(self):items=list(self.pending);self.pending.clear();return items
        async def close(self):pass
    source=save(Source(name='Delayed model-result protocol test',kind='phone'))
    session=VideoSession(source);session.detector=Quiet();sessions[source.id]=session
    jpeg=encode(np.full((48,64,3),120,np.uint8))
    try:
        for index in range(91):await session.push(Frame(index*.5,index,encode(np.full((48,64,3),90+index,np.uint8))))
        session.detector.pending.extend([detection(3,4,[6,8],action),detection(4,5,[8,10],action)])
        session.collect_detections(45)
        assert session.review_alert['received_stream_time']==45
        await session.push(Frame(45.5,91,jpeg))
    finally:await session.close()
    record=next(i for i in rows(Incident) if i.source_id==source.id)
    assert record.start_time==0 and record.end_time==expected_end
    assert digest(settings.data_dir/'incidents'/record.id/'original.mp4')==record.record['original_sha256']
    assert session.review_alerts[0]['status']=='saved'
    assert session.source.telemetry['review_alerts'][0]['incident_id']==record.id

def test_augmentation_never_changes_held_out_inputs_or_splits(tmp_path):
    root=tmp_path/'parent';root.mkdir();records=[]
    for split in ['train','val','test']:
        paths=[];hashes=[]
        for n in range(4):
            path=root/f'{split}-{n}.jpg';Image.new('RGB',(32,32),(90+n,120,140)).save(path)
            paths.append(path.name);hashes.append(digest(path))
        target=detection(1,4,[0,1,2,3]).model_dump()
        records.append({'id':split,'group_id':split,'split':split,'frames':paths,'frame_sha256':hashes,'timestamps':[1,2,3,4],'target':target})
    (root/'records.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    (root/'provenance.json').write_text(json.dumps({'records_sha256':digest(root/'records.jsonl')}))
    out=tmp_path/'augmented';meta=augment(root,out)
    result=[json.loads(x) for x in (out/'records.jsonl').read_text().splitlines()]
    assert len(result)==6 and meta['training_source_videos']==1
    for split in ['val','test']:
        r=next(r for r in result if r['split']==split)
        assert r['frame_sha256']==next(r for r in records if r['split']==split)['frame_sha256']
    for r in result:
        assert r['group_id']==r['split']
        if r['augmentation']['model_time_augmented']:
            assert set(r['target']['supporting_frames'])<=set(r['frame_indices'])
