"""Observable, cancellable training; candidate weights never become live here."""
import asyncio
from copy import deepcopy
import json
import math
from pathlib import Path
from threading import RLock
import time

from .db import Job, get, rows, save, now

ACTIVE = {'queued','running','preparing','loading','training','evaluating','publishing','cancelling'}
TERMINAL = {'completed','cancelled','failed'}
STAGES = {'queued','preparing','loading','training','evaluating','publishing','completed','cancelling','cancelled','failed'}
record_lock = RLock()
controls = {}
live_return = {'status':'idle','warmup_pending':False}
warm_task = None


class TrainingCancelled(RuntimeError):
    pass


def update_workflow(run_id, stage, status='running', message='', progress=None):
    from .learning import LearningRun
    if stage not in STAGES or status not in {'queued','running','completed','cancelling','cancelled','failed'}:
        raise ValueError('Unknown training workflow stage/status')
    with record_lock:
        run = get(LearningRun,run_id)
        if not run:
            raise ValueError('Training run does not exist')
        old = dict(run.record.get('workflow',{}))
        if (run.status=='cancelled' or old.get('cancel_requested')) and stage not in {'cancelling','cancelled'}:
            return run
        events = list(old.get('events',[]))
        message = str(message)[:500]
        if not events or any(events[-1].get(k)!=v for k,v in {'stage':stage,'status':status,'message':message}.items()):
            events.append({'timestamp':now(),'stage':stage,'status':status,'message':message})
        workflow = {**old,'stage':stage,'status':status,'updated_at':now(),'events':events[-80:]}
        if stage!=old.get('stage') and progress is None:
            workflow.pop('progress',None)
        if progress is not None:
            clean={}
            for key in ('step','total_steps','loss'):
                value=progress.get(key)
                if type(value) in (int,float) and math.isfinite(value) and value>=0:
                    clean[key]=value
            workflow['progress']=clean
        run.record={**run.record,'workflow':workflow,'deployed':False}
        return save(run)


def raise_if_cancelled(run_id):
    from .learning import LearningRun
    run=get(LearningRun,run_id)
    if not run or run.status in {'cancelled','cancelling'} or run.record.get('workflow',{}).get('cancel_requested'):
        raise TrainingCancelled('Training cancelled; live model is unchanged')


def register(run_id):
    event=asyncio.Event()
    controls[run_id]=event
    try:raise_if_cancelled(run_id)
    except TrainingCancelled:event.set()
    return event


def unregister(run_id):
    controls.pop(run_id,None)


def mark_cancelled(run_id,reason):
    from .learning import LearningRun
    with record_lock:
        run=get(LearningRun,run_id)
        if run and run.status!='completed':
            run.status='cancelled'
            run.record={**run.record,'summary':'Training cancelled; selected live weights are unchanged.',
                'cancellation_reason':str(reason)[:300],'deployed':False}
            save(run)
            return update_workflow(run_id,'cancelled','cancelled',reason)
    return run


async def cancel_run(run_id,reason='Reviewer requested cancellation'):
    from .learning import LearningRun
    with record_lock:
        run=get(LearningRun,run_id)
        if not run:raise ValueError('Training run does not exist')
        if run.status in TERMINAL:return run,False
        associated=[j for j in rows(Job) if j.kind=='learning_train' and j.incident_id==run_id and j.status in {'queued','running','cancelling'}]
        running=run_id in controls or any(j.status in {'running','cancelling'} for j in associated)
        run.record={**run.record,'workflow':{**run.record.get('workflow',{}),'cancel_requested':True},'deployed':False}
        run.status='cancelling' if running else 'cancelled'
        save(run)
        update_workflow(run_id,'cancelling' if running else 'cancelled','cancelling' if running else 'cancelled',reason)
        for job in associated:
            if job.status=='queued':
                job.status='cancelled';job.ended_at=now();job.result={'status':'cancelled','reason':reason,'deployed':False}
            else:job.status='cancelling'
            save(job)
        event=controls.get(run_id)
        if event:event.set()
        if not running:run=mark_cancelled(run_id,reason)
        return get(LearningRun,run_id),True


async def cancel_active_training(reason='Return to live requested',wait=True,timeout=12):
    from .learning import LearningRun
    ids=[]
    for run in rows(LearningRun):
        if run.status in ACTIVE:
            _,requested=await cancel_run(run.id,reason)
            if requested:ids.append(run.id)
    if wait:
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            # Controls disappear only after the training subprocess is reaped.
            active=[j for j in rows(Job) if j.kind=='learning_train' and j.incident_id in ids and j.status in {'running','cancelling'}]
            if not any(i in controls for i in ids) and not active:return ids
            await asyncio.sleep(.1)
        raise TimeoutError('Training cancellation is still releasing GPU resources')
    return ids


def progress_reader(run_id,path):
    last=None
    def read():
        nonlocal last
        try:
            file=Path(path)
            if not file.is_file() or file.stat().st_size>16384:return
            raw=file.read_text()
            if raw==last:return
            value=json.loads(raw)
            if not isinstance(value,dict) or value.get('stage') not in {'loading','training','evaluating'}:return
            raise_if_cancelled(run_id)
            update_workflow(run_id,value['stage'],'running',value.get('message',''),value)
            last=raw
        except (OSError,ValueError,json.JSONDecodeError):pass
    return read


async def _warm_for_live():
    from . import jobs
    from .runtime import sessions
    from .config import settings
    jobs.live_warmup=True
    owns_gpu=False
    try:
        while jobs.gpu_busy:
            live_return.update(status='waiting_for_gpu',warmup_pending=True)
            await asyncio.sleep(.2)
        # An active source warms its own detector. Do not compete with its calls.
        if sessions:
            live_return.update(status='ready',warmup_pending=False)
            return
        jobs.gpu_busy=True;owns_gpu=True
        live_return.update(status='warming',warmup_pending=True)
        if settings.streaming_model!='fixture':
            from ml.service import model_service
            await model_service.ensure()
            if settings.observation_verifier_model=='qwen3vl':
                from ml.verifier import acquire_verifier
                await asyncio.to_thread(acquire_verifier,settings.verifier_checkpoint)
            import httpx
            health=settings.moss_ws_url.replace('ws://','http://').replace('wss://','https://').split('/v1/')[0]+'/health'
            async with httpx.AsyncClient(timeout=3) as client:
                response=await client.get(health);response.raise_for_status()
                if not response.json().get('model_loaded'):raise RuntimeError('MOSS is not loaded')
        live_return.update(status='ready',warmup_pending=False)
    except asyncio.CancelledError:
        live_return.update(status='idle',warmup_pending=False)
        raise
    except Exception:
        live_return.update(status='failed',warmup_pending=False,error='Live model warm-up failed; inspect backend model status')
    finally:
        if owns_gpu:jobs.gpu_busy=False
        jobs.live_warmup=False


async def return_to_live():
    global warm_task
    if warm_task is None or warm_task.done():
        live_return.clear();live_return.update(status='cancelling',warmup_pending=True)
    ids=await cancel_active_training()
    if warm_task is None or warm_task.done():
        warm_task=asyncio.create_task(_warm_for_live())
    return {**deepcopy(live_return),'cancelled_run_ids':ids}


async def shutdown():
    global warm_task
    if warm_task and not warm_task.done():
        warm_task.cancel()
        await asyncio.gather(warm_task,return_exceptions=True)
    warm_task=None


def recover_training_runs():
    """Repair stale workflow rows after db.init has recovered interrupted jobs.

    A hard crash can leave an untracked subprocess; this repairs metadata only
    and explicitly avoids claiming that an unknown GPU process was terminated.
    """
    from .learning import LearningRun
    recovered=[]
    with record_lock:
        all_jobs=rows(Job)
        for run in rows(LearningRun):
            if run.status not in ACTIVE:continue
            associated=[job for job in all_jobs if job.kind=='learning_train' and job.incident_id==run.id]
            if any(job.status in {'queued','running'} for job in associated):continue
            requested=run.record.get('workflow',{}).get('cancel_requested') or run.status=='cancelling'
            stage='cancelled' if requested else 'failed'
            message='Backend restarted during training/preparation. Verify GPU process status before retrying; selected live weights were not changed.'
            run.status=stage
            run.record={**run.record,'recovery_warning':message,'deployed':False,'summary':message}
            save(run)
            update_workflow(run.id,stage,stage,message)
            for job in associated:
                if job.status=='cancelling':
                    job.status='cancelled';job.ended_at=now();job.result={'status':'cancelled','recovery_warning':message,'deployed':False};save(job)
            recovered.append(run.id)
    return recovered
