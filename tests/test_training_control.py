import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from apps.api.db import Job, init, save, get
from apps.api.learning import LearningRun
from apps.api.main import app
from apps.api.config import settings
from apps.api import jobs, training_control as control


@pytest.fixture(autouse=True)
def isolate_control_records():
    from sqlmodel import Session
    from apps.api.db import engine,rows
    init(recover=False)
    old_runs={r.id for r in rows(LearningRun)}
    old_jobs={j.id for j in rows(Job)}
    yield
    with Session(engine) as session:
        for cls,old in ((Job,old_jobs),(LearningRun,old_runs)):
            for item in rows(cls):
                if item.id not in old:
                    if cls is LearningRun:control.unregister(item.id)
                    obj=session.get(cls,item.id)
                    if obj:session.delete(obj)
        session.commit()


def run(status='queued'):
    init(recover=False)
    return save(LearningRun(snapshot_id='control-test',status=status,record={'test_control':True}))


@pytest.mark.asyncio
async def test_queued_cancellation_is_durable_idempotent_and_never_deploys():
    item=run();job=save(Job(incident_id=item.id,kind='learning_train'))
    control.update_workflow(item.id,'queued','queued','Waiting')
    cancelled,requested=await control.cancel_run(item.id)
    assert requested and cancelled.status=='cancelled'
    assert get(Job,job.id).status=='cancelled'
    assert cancelled.record['workflow']['stage']=='cancelled'
    assert cancelled.record['deployed'] is False
    with pytest.raises(control.TrainingCancelled):control.raise_if_cancelled(item.id)
    again,requested=await control.cancel_run(item.id)
    assert not requested and again.status=='cancelled'
    control.update_workflow(item.id,'completed','completed','Must not overwrite cancellation')
    assert get(LearningRun,item.id).record['workflow']['stage']=='cancelled'


def test_workflow_real_progress_is_persisted_bounded_and_filters_unknown_fields(tmp_path):
    item=run('running');progress=tmp_path/'progress.json'
    reader=control.progress_reader(item.id,progress)
    progress.write_text(json.dumps({'stage':'training','step':4,'total_steps':20,'loss':.25,'token':'not-stored'}))
    reader()
    saved=get(LearningRun,item.id)
    assert saved.record['workflow']['progress']=={'step':4,'total_steps':20,'loss':.25}
    assert 'not-stored' not in str(saved.record)
    count=len(saved.record['workflow']['events']);reader()
    assert len(get(LearningRun,item.id).record['workflow']['events'])==count
    for i in range(100):control.update_workflow(item.id,'training','running',str(i),{'step':i})
    assert len(get(LearningRun,item.id).record['workflow']['events'])==80
    # A partial file while the training writer replaces it is ignored.
    progress.write_text('{');reader()


@pytest.mark.asyncio
async def test_command_cancellation_reaps_parent_and_kills_ignoring_child(tmp_path):
    info=tmp_path/'pids.json';event=asyncio.Event()
    child="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
    code="import os,subprocess,sys,time,json;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);Path(sys.argv[1]).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)"
    task=asyncio.create_task(jobs.command([sys.executable,'-c',code,info,child],cancel_event=event))
    end=time.monotonic()+5
    while not info.exists() and time.monotonic()<end:await asyncio.sleep(.02)
    assert info.exists()
    parent,descendant=json.loads(info.read_text())
    started=time.monotonic();event.set()
    with pytest.raises(control.TrainingCancelled):await asyncio.wait_for(task,8)
    assert time.monotonic()-started<8
    # An adopted zombie is dead and holds no CUDA/process resources.
    for pid in (parent,descendant):
        result=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True)
        assert not result.stdout.strip() or result.stdout.strip().startswith('Z')


@pytest.mark.asyncio
async def test_prelaunch_cancel_does_not_execute_any_process(tmp_path):
    event=asyncio.Event();event.set();target=tmp_path/'should-not-exist'
    with pytest.raises(control.TrainingCancelled):
        await jobs.command([sys.executable,'-c','from pathlib import Path;import sys;Path(sys.argv[1]).touch()',target],cancel_event=event)
    assert not target.exists()


@pytest.mark.asyncio
async def test_cancel_active_training_waits_for_gpu_owner_and_leaves_other_jobs(monkeypatch):
    item=run('running');job=save(Job(incident_id=item.id,kind='learning_train',status='running'))
    other=save(Job(incident_id='other-review',kind='relayvsr',status='queued'))
    event=control.register(item.id)
    async def finish_owner():
        await event.wait();await asyncio.sleep(.05)
        control.mark_cancelled(item.id,'Stopped')
        row=get(Job,job.id);row.status='cancelled';save(row)
        control.unregister(item.id)
    task=asyncio.create_task(finish_owner())
    ids=await control.cancel_active_training(timeout=2)
    await task
    assert item.id in ids and get(Job,other.id).status=='queued'
    assert get(LearningRun,item.id).status=='cancelled'


def test_cancel_and_return_to_live_endpoints_require_auth_and_summary_advertises_control():
    with TestClient(app) as client:
        item=run()
        assert client.post('/api/learning/runs/'+item.id+'/cancel').status_code==401
        assert client.post('/api/learning/return-to-live').status_code==401
        client.cookies.set('safesc_token',settings.app_token)
        response=client.post('/api/learning/runs/'+item.id+'/cancel')
        assert response.status_code==200 and response.json()['run']['status']=='cancelled'
        assert client.post('/api/learning/runs/missing/cancel').status_code==404
        assert client.get('/api/learning/summary').json()['workflow_control'] is True


def setup_job(monkeypatch,tmp_path):
    from apps.api import learning
    from apps.api.learning import DatasetSnapshot
    manifest=tmp_path/'snapshot.json';manifest.write_text('{}')
    snapshot=save(DatasetSnapshot(sha256='test-hash',path=str(manifest)))
    item=save(LearningRun(snapshot_id=snapshot.id,status='queued',record={
        'resume_adapter':'test-parent','steps':20,'learning_rate':1e-5}))
    job=save(Job(incident_id=item.id,kind='learning_train',status='running'))
    monkeypatch.setattr(learning,'validate_snapshot_current',lambda _:None)
    monkeypatch.setattr(learning,'run_examples',lambda _:(tmp_path/'bundle',[]))
    monkeypatch.setattr(learning,'folder',lambda:tmp_path)
    return learning,item,job


@pytest.mark.asyncio
async def test_running_run_cancellation_never_becomes_completed_or_changes_adapter(monkeypatch,tmp_path):
    learning,item,job=setup_job(monkeypatch,tmp_path)
    selected=settings.verifier_adapter;started=asyncio.Event()
    async def fake_command(argv,**kwargs):
        started.set();await kwargs['cancel_event'].wait()
        raise control.TrainingCancelled('Stopped in test')
    monkeypatch.setattr(jobs,'command',fake_command)
    task=asyncio.create_task(learning.run_training_job(job))
    await started.wait();await control.cancel_run(item.id)
    with pytest.raises(control.TrainingCancelled):await task
    saved=get(LearningRun,item.id)
    assert saved.status=='cancelled' and saved.record['workflow']['stage']=='cancelled'
    assert saved.record['deployed'] is False
    assert settings.verifier_adapter==selected and item.id not in control.controls


@pytest.mark.asyncio
async def test_registry_failure_keeps_evaluated_candidate_and_selected_adapter(monkeypatch,tmp_path):
    learning,item,job=setup_job(monkeypatch,tmp_path)
    monkeypatch.setenv('HF_ADAPTER_REPO','private-test/adapter')
    selected=settings.verifier_adapter;calls=[]
    async def fake_command(argv,**kwargs):
        calls.append(list(map(str,argv)))
        output=tmp_path/'candidates'/item.id;output.mkdir(parents=True,exist_ok=True)
        if 'scripts.train_learning' in argv:
            (output/'evaluation.json').write_text(json.dumps({'training_examples':4,'unique_training_videos':1,
                'nonregression':False,'adapter_sha256':'test-sha','improved_over_parent':False}))
            return {'exit_code':0}
        raise RuntimeError('Private registry unavailable')
    monkeypatch.setattr(jobs,'command',fake_command)
    result=await learning.run_training_job(job)
    saved=get(LearningRun,item.id)
    assert len(calls)==2 and saved.status=='completed'
    assert saved.record['registry']['status']=='failed'
    assert saved.record['evaluation']['adapter_sha256']=='test-sha'
    assert any(e['stage']=='evaluating' and e['status']=='completed' for e in saved.record['workflow']['events'])
    assert any(e['stage']=='publishing' and e['status']=='failed' for e in saved.record['workflow']['events'])
    assert result['deployed'] is False and settings.verifier_adapter==selected


@pytest.mark.asyncio
async def test_cancel_still_kills_child_after_parent_has_already_exited(tmp_path):
    info=tmp_path/'child.json';event=asyncio.Event()
    child="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"
    code="import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);Path(sys.argv[1]).write_text(str(p.pid))"
    task=asyncio.create_task(jobs.command([sys.executable,'-c',code,info,child],cancel_event=event))
    deadline=time.monotonic()+3
    while not info.exists() and time.monotonic()<deadline:await asyncio.sleep(.02)
    await asyncio.sleep(.1)
    event.set()
    with pytest.raises(control.TrainingCancelled):await asyncio.wait_for(task,8)
    result=subprocess.run(['ps','-o','stat=','-p',info.read_text()],capture_output=True,text=True)
    assert not result.stdout.strip() or result.stdout.strip().startswith('Z')


def test_restart_recovery_preserves_queued_jobs_and_flags_unverified_gpu_processes():
    interrupted=run('running')
    save(Job(incident_id=interrupted.id,kind='learning_train',status='failed'))
    queued=run('queued');save(Job(incident_id=queued.id,kind='learning_train',status='queued'))
    preparing=run('preparing')
    recovering=run('cancelling');recovering.record={'workflow':{'cancel_requested':True}};save(recovering)
    job=save(Job(incident_id=recovering.id,kind='learning_train',status='cancelling'))
    ids=control.recover_training_runs()
    assert interrupted.id in ids and preparing.id in ids and recovering.id in ids
    assert queued.id not in ids and get(LearningRun,queued.id).status=='queued'
    assert get(LearningRun,interrupted.id).status=='failed'
    assert 'Verify GPU process status' in get(LearningRun,interrupted.id).record['recovery_warning']
    assert get(LearningRun,recovering.id).status=='cancelled' and get(Job,job.id).status=='cancelled'
