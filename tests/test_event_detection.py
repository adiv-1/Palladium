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

def test_constraint_cache_reuses_vocabulary_but_never_evidence_parser(monkeypatch):
    import sys
    from ml.verifier import StructuredVisualVerifier
    builds=[];parsers=[]
    def vocabulary(tokenizer):
        result=object();builds.append((tokenizer,result));return result
    def parser(schema):
        value={'schema':copy.deepcopy(schema)};parsers.append(value);return value
    monkeypatch.setitem(sys.modules,'lmformatenforcer',SimpleNamespace(JsonSchemaParser=parser))
    monkeypatch.setitem(sys.modules,'lmformatenforcer.integrations.transformers',SimpleNamespace(
        build_token_enforcer_tokenizer_data=vocabulary,
        build_transformers_prefix_allowed_tokens_fn=lambda data,state:(data,state)))
    verifier=StructuredVisualVerifier.__new__(StructuredVisualVerifier)
    verifier.processor=SimpleNamespace(tokenizer=object())
    first=verifier.constraint_prefix({'ids':[1,2]})
    second=verifier.constraint_prefix({'ids':[3,4]})
    assert len(builds)==1 and first[0] is second[0]
    assert first[1] is not second[1]
    assert first[1]['schema']=={'ids':[1,2]} and second[1]['schema']=={'ids':[3,4]}
    verifier.processor.tokenizer=object()
    third=verifier.constraint_prefix({'ids':[5,6]})
    assert len(builds)==2 and third[0] is not first[0]

@pytest.mark.asyncio
async def test_brief_action_during_slow_verification_remains_in_next_evidence(monkeypatch):
    """One action at t=5 survives a preceding check finishing at t=25."""
    from apps.api.video import decode
    monkeypatch.setattr(settings,'enable_tracking',False)
    monkeypatch.setattr(settings,'enhancement_model','bypass')
    monkeypatch.setattr(settings,'verification_frame_count',4)
    monkeypatch.setattr(settings,'max_inference_lag_seconds',60)
    class Detector:
        last_verified=1
        async def observe(self,*args):pass
    session=VideoSession(Source(name='Brief action while GPU occupied',kind='phone'))
    session.detector=Detector()
    jpeg=encode(np.full((48,64,3),90,np.uint8))
    for i in range(251):
        session.buffer.append(Frame(i*.1,i,jpeg,motion=100 if i==50 else 0))
    latest=session.buffer.frames[-1]
    await session.observe(latest,decode(jpeg),{'state':'well_lit'})
    evidence=session.detector.evidence_frames
    assert len(evidence)==4
    assert 50 in [f.index for f in evidence]
    assert [f.timestamp for f in evidence]==sorted(f.timestamp for f in evidence)
    assert all(f in session.buffer.frames for f in evidence)
    # Preserve the configured stale-evidence bound rather than growing a backlog.
    for i in range(251,801):session.buffer.append(Frame(i*.1,i,jpeg))
    await session.observe(session.buffer.frames[-1],decode(jpeg),{'state':'well_lit'})
    assert session.detector.evidence_pool[0].timestamp==20
    assert all(f.timestamp>=20 for f in session.detector.evidence_frames)

@pytest.mark.asyncio
async def test_short_clip_final_verifier_uses_buffer_tail_after_slow_model_start(monkeypatch):
    """The clip may finish while its first inference call is still connecting."""
    monkeypatch.setattr(settings,'verification_frame_count',4)
    init()
    class SlowStartDetector:
        last_verified=-1e9
        evidence_frames=[]
        evidence_pool=[]
        async def finish(self):self.final_frames=list(self.evidence_frames)
        async def close(self):pass
        def drain(self):return []
    session=VideoSession(save(Source(name='Short input completed during model startup',kind='upload')))
    detector=SlowStartDetector();session.detector=detector
    jpeg=encode(np.full((48,64,3),100,np.uint8))
    for i in range(41):session.buffer.append(Frame(i*.1,100+i,jpeg))
    # Only the first frame reached observe() while the rest accumulated in RAM.
    detector.evidence_frames=[session.buffer.frames[0]]
    detector.evidence_pool=[session.buffer.frames[0]]
    await session.close()
    assert len(detector.final_frames)==4
    assert detector.final_frames[0].index==100
    assert detector.final_frames[-1].index==140
    assert detector.final_frames[-1].timestamp==4
    assert all(f in session.buffer.frames for f in detector.final_frames)
    assert session.source.telemetry['stream_ended'] is True
    assert session.source.telemetry['capture_active'] is False

@pytest.mark.asyncio
async def test_final_window_uses_stream_end_not_early_motion_selected_end(monkeypatch):
    monkeypatch.setattr(settings,'verification_frame_count',4)
    detector=MossDetector('ws://unused');detector.use_verifier=True
    detector.last_verified=3.5
    detector.evidence_pool=[Frame(i*.1,i,b'',motion=100 if i==10 else 0) for i in range(41)]
    detector.evidence_frames=select_evidence(detector.evidence_pool,4)
    assert detector.evidence_frames[-1].timestamp<detector.last_verified
    calls=[]
    async def verify(frames,notes,tracks,pool=None,kind='live_window'):
        calls.append((list(frames),kind))
    monkeypatch.setattr(detector,'verify_window',verify)
    await detector.finish()
    assert len(calls)==1 and calls[0][1]=='final_window'
    frames=calls[0][0]
    assert len(frames)==4
    assert frames[-1].index==40 and frames[-1].timestamp==4
    assert all(f in detector.evidence_pool for f in frames)
    assert detector.last_verified==4
    await detector.finish()
    assert len(calls)==1 # close() calling finish() again does not duplicate work.
