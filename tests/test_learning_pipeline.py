import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from sqlmodel import create_engine

from apps.api import db
from apps.api.config import settings
from apps.api.learning import (AnnotationInput,LearningPermission,LearningRun,
    annotate,build_snapshot,register_local)
from apps.api.learning_pipeline import prepare_training_run
from apps.api.video import Frame,encode,write_clip,digest
from scripts.prepare_learning_training import prepare


@pytest.fixture
def repository(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    monkeypatch.setattr(settings,'verifier_adapter','evaluated-parent-adapter')
    monkeypatch.setattr(db,'engine',create_engine(f'sqlite:///{tmp_path}/pipeline.sqlite',connect_args={'check_same_thread':False}))
    db.init()
    def add(name,split='train',permitted=True,group=None):
        path=tmp_path/(name+'.mp4')
        offset=sum(name.encode())%100
        write_clip([Frame(i/10,i,encode(np.full((48,80,3),40+offset+i,np.uint8))) for i in range(20)],path)
        asset=register_local(path,name,group or name,'upload',provenance={'official_split':split})
        if permitted:db.save(LearningPermission(subject='asset:'+asset.id,permission='permitted',note='Owner permits this staged research recording.'))
        annotate(asset,AnnotationInput(action='object_taking',start=.1,end=1.6,description='A person lifts and carries an object.'))
        return asset
    return add


def payload():return SimpleNamespace(steps=20,learning_rate=.00001)


def test_newly_approved_recording_enters_fresh_run_instead_of_old_completed_bundle(repository,tmp_path):
    originals=[repository('original-'+s,s) for s in ('train','val','test')]
    previous=build_snapshot();old=tmp_path/'old-bundle';prepare(previous.path,old)
    parent=db.save(LearningRun(snapshot_id=previous.id,status='completed',record={'data_relative_path':'old-bundle'}))
    added=repository('newly-approved-video')
    excluded=repository('permission-unknown',permitted=False)
    run=prepare_training_run(payload())
    assert run.status=='queued' and run.snapshot_id!=previous.id
    assert run.record['fresh_snapshot'] is True and run.record['parent_run_id']==parent.id
    root=tmp_path/'learning'/run.record['data_relative_path']
    records=[json.loads(line) for line in (root/'records.jsonl').read_text().splitlines()]
    assert added.id in {r.get('asset_id') for r in records}
    assert excluded.id not in {r.get('asset_id') for r in records}
    assert run.record['unique_training_videos']==2
    assert run.record['training_examples']==8
    assert digest(root/'records.jsonl')==run.record['records_sha256']
    previous_records=[json.loads(line) for line in (old/'records.jsonl').read_text().splitlines()]
    for previous_record in previous_records:
        current=next(r for r in records if r['id']==previous_record['id'])
        assert current['split']==previous_record['split']
        assert current['frame_sha256']==previous_record['frame_sha256']
    for record in records:
        for frame,expected in zip(record['frames'],record['frame_sha256']):assert digest(root/frame)==expected
    assert {a.id for a in originals}<={r.get('asset_id') for r in records}
    assert not db.rows(db.Job) # Preparation never launches optimization itself.


def test_fresh_preparation_requires_current_permission_and_original_hash(repository,tmp_path):
    asset=repository('approved-recording')
    snapshot=build_snapshot()
    db.save(LearningPermission(subject='asset:'+asset.id,permission='denied',note='Owner revokes permission before extraction.'))
    with pytest.raises(ValueError,match='no-longer-permitted'):prepare(snapshot.path,tmp_path/'revoked')
    assert not (tmp_path/'revoked').exists()
    db.save(LearningPermission(subject='asset:'+asset.id,permission='permitted',note='Owner permits reuse again.'))
    path=Path(asset.path);path.chmod(0o600);path.write_bytes(b'tampered original')
    with pytest.raises(ValueError,match='hash mismatch'):build_snapshot()


def test_preparation_refuses_recording_group_leakage(repository):
    repository('first-recording','train',group='same-source')
    repository('derived-recording','val',group='same-source')
    with pytest.raises(ValueError,match='multiple dataset splits'):prepare_training_run(payload())
    run=db.rows(LearningRun)[-1]
    assert run.status=='failed' and run.record['workflow']['stage']=='failed'


def test_cancel_during_frame_preparation_never_resets_run_to_queued(repository,monkeypatch):
    import apps.api.learning_pipeline as pipeline
    from apps.api.training_control import TrainingCancelled,cancel_run
    for split in ('train','val','test'):repository('cancel-'+split,split)
    actual=pipeline.prepare
    def cancel_before_export(manifest,out,check_cancelled=None):
        run=next(r for r in db.rows(LearningRun) if r.status=='preparing')
        asyncio.run(cancel_run(run.id))
        return actual(manifest,out,check_cancelled=check_cancelled)
    monkeypatch.setattr(pipeline,'prepare',cancel_before_export)
    with pytest.raises(TrainingCancelled):prepare_training_run(payload())
    run=db.rows(LearningRun)[-1]
    assert run.status=='cancelled'
    assert run.record['workflow']['cancel_requested'] is True
    assert not db.rows(db.Job)


def test_training_frames_apply_the_same_source_rotation_as_video_analysis(repository,tmp_path,monkeypatch):
    asset=repository('rotated-source')
    snapshot=build_snapshot()
    import apps.api.video as video
    pixels=np.zeros((48,80,3),np.uint8);pixels[:,:40]=[20,80,200]
    class PhoneFrame:
        rotation=-90
        def __init__(self,index):self.time=index/10
        def to_ndarray(self,format):return pixels.copy()
    class Container:
        streams=SimpleNamespace(video=[SimpleNamespace(average_rate=10)])
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def decode(self,*args):return iter(PhoneFrame(i) for i in range(20))
    monkeypatch.setattr(video.av,'open',lambda *args,**kwargs:Container())
    out=tmp_path/'oriented';prepare(snapshot.path,out)
    record=json.loads((out/'records.jsonl').read_text().splitlines()[0])
    assert record['id']==asset.id
    assert record['frame_orientation']=='source display rotation applied by read_video'
    assert len(record['source_frame_indices'])==4
    for path in record['frames']:
        with Image.open(out/path) as image:assert image.size==(48,80)


def test_fresh_dataset_preparation_needs_no_completed_experiment(repository):
    for split in ('train','val','test'):repository('first-run-'+split,split)
    run=prepare_training_run(payload())
    assert run.status=='queued' and run.record['fresh_snapshot'] is True
    assert 'parent_run_id' not in run.record
    assert run.record['snapshot_counts']=={'train':1,'val':1,'test':1}


def test_cancellation_after_augmentation_wins_before_final_queue_transition(repository,monkeypatch):
    import apps.api.learning_pipeline as pipeline
    from apps.api.training_control import TrainingCancelled,cancel_run
    for split in ('train','val','test'):repository('late-cancel-'+split,split)
    actual=pipeline.augment
    def augment_then_cancel(source,out):
        result=actual(source,out)
        run=next(r for r in db.rows(LearningRun) if r.status=='preparing')
        asyncio.run(cancel_run(run.id))
        return result
    monkeypatch.setattr(pipeline,'augment',augment_then_cancel)
    with pytest.raises(TrainingCancelled):prepare_training_run(payload())
    run=db.rows(LearningRun)[-1]
    assert run.status=='cancelled' and run.record['workflow']['cancel_requested'] is True
    assert not (settings.data_dir/'learning'/'datasets'/run.id).exists()
    assert not db.rows(db.Job)


@pytest.mark.asyncio
async def test_automatic_training_is_opt_in_idempotent_and_never_activates(repository,monkeypatch):
    import stat
    from apps.api import learning
    from apps.api.learning_pipeline import get_automation_policy,set_automation_policy,maybe_queue_after_annotation
    asset=repository('automation-train')
    annotation=learning.latest_annotations()[asset.id]
    calls=[]
    async def queue(request):
        calls.append(request.steps)
        return {'run':db.save(LearningRun(snapshot_id='test',status='completed',record={}))}
    monkeypatch.setattr(learning,'queue_training',queue)
    assert get_automation_policy()['enabled'] is False
    assert (await maybe_queue_after_annotation(asset,annotation))['reason']=='disabled'
    assert not calls
    set_automation_policy(True)
    assert (await maybe_queue_after_annotation(asset,annotation))['queued'] is True
    assert calls==[20]
    assert (await maybe_queue_after_annotation(asset,annotation))['reason']=='already_triggered'
    policy=get_automation_policy()
    assert policy['automatic_activation'] is False
    assert annotation.id in policy['triggered_annotation_ids']
    assert stat.S_IMODE((settings.data_dir/'learning'/'automation.json').stat().st_mode)==0o600
    assert db.rows(LearningRun)[-1].record['deployed'] is False


@pytest.mark.asyncio
async def test_automation_excludes_held_out_unpermitted_and_noncurrent_labels(repository,monkeypatch):
    from apps.api import learning
    from apps.api.learning_pipeline import set_automation_policy,maybe_queue_after_annotation
    async def forbidden(_):raise AssertionError('Ineligible annotation must not queue optimization')
    monkeypatch.setattr(learning,'queue_training',forbidden)
    set_automation_policy(True)
    for split in ('val','test'):
        asset=repository('held-out-'+split,split)
        assert (await maybe_queue_after_annotation(asset,learning.latest_annotations()[asset.id]))['reason']=='ineligible'
    asset=repository('unpermitted-automatic',permitted=False)
    assert (await maybe_queue_after_annotation(asset,learning.latest_annotations()[asset.id]))['reason']=='ineligible'
    asset=repository('superseded-automatic')
    old=learning.latest_annotations()[asset.id]
    annotate(asset,AnnotationInput(action='object_taking',start=.1,end=1.6,description='A person retrieves an object.',status='pending'))
    assert (await maybe_queue_after_annotation(asset,old))['reason']=='ineligible'


@pytest.mark.asyncio
async def test_automation_does_not_queue_during_an_active_run_and_records_failures(repository,monkeypatch):
    from apps.api import learning
    from apps.api.learning_pipeline import set_automation_policy,get_automation_policy,maybe_queue_after_annotation
    asset=repository('automatic-busy');annotation=learning.latest_annotations()[asset.id]
    set_automation_policy(True)
    active=db.save(LearningRun(snapshot_id='test',status='preparing'))
    assert (await maybe_queue_after_annotation(asset,annotation))['reason']=='training_active'
    active.status='cancelled';db.save(active)
    async def fail(_):raise ValueError('No separate held-out dataset is approved.')
    monkeypatch.setattr(learning,'queue_training',fail)
    assert (await maybe_queue_after_annotation(asset,annotation))['reason']=='preparation_failed'
    assert 'held-out' in get_automation_policy()['last_error']
    assert annotation.id not in get_automation_policy()['triggered_annotation_ids']


def test_preparation_atomically_reserves_one_active_experiment(repository):
    db.save(LearningRun(snapshot_id='another-preparation',status='preparing'))
    with pytest.raises(ValueError,match='already queued or running'):prepare_training_run(payload())
    assert len(db.rows(LearningRun))==1
