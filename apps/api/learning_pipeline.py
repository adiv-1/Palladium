"""Prepare each requested experiment from the currently approved repository."""
import json
import shutil
from pathlib import Path

from .config import settings
from .db import get,rows,save
from .learning import LearningRun,build_snapshot,folder,validate_snapshot_current
from .training_control import (TrainingCancelled,mark_cancelled,raise_if_cancelled,
    record_lock,update_workflow)
from scripts.prepare_learning_training import prepare
from scripts.augment_event_training import augment


def _patch(run_id,*,snapshot_id=None,status=None,record=None):
    # Cancellation and preparation update the same record under the same lock.
    # Always merge a fresh row so a cancel request cannot be overwritten.
    with record_lock:
        raise_if_cancelled(run_id)
        run=get(LearningRun,run_id)
        if snapshot_id is not None:run.snapshot_id=snapshot_id
        if status is not None:run.status=status
        if record:run.record={**run.record,**record}
        return save(run)


def prepare_training_run(payload):
    """Build a fresh snapshot and four-frame bundle; never start or deploy training."""
    from .training_control import ACTIVE
    with record_lock:
        if any(r.status in ACTIVE for r in rows(LearningRun)):
            raise ValueError('A training run is already queued or running')
        run=save(LearningRun(snapshot_id='preparing',status='preparing',record={
            'model':'Qwen3-VL-4B LoRA reviewed-video experiment','deployed':False,
            'summary':'Preparing a fresh dataset from current permissions and approved labels.'}))
    source=folder()/'prepared'/run.id
    output=folder()/'datasets'/run.id
    try:
        update_workflow(run.id,'preparing','running','Building a fresh approved dataset snapshot.',{'step':0,'total_steps':3})
        raise_if_cancelled(run.id)
        if not settings.verifier_adapter:raise ValueError('An evaluated parent adapter is required')
        snapshot=build_snapshot()
        _patch(run.id,snapshot_id=snapshot.id)
        update_workflow(run.id,'preparing','running','Extracting four original, correctly oriented frames per approved interval.',{'step':1,'total_steps':3})
        prepared=prepare(snapshot.path,source,check_cancelled=lambda:raise_if_cancelled(run.id))
        raise_if_cancelled(run.id)
        counts=prepared['counts']
        if not all(counts.get(split,0)>0 for split in ('train','val','test')):
            raise ValueError('Current approved videos must include separate training, validation, and test splits')
        update_workflow(run.id,'preparing','running','Augmenting training examples; validation and test frames remain unchanged.',{'step':2,'total_steps':3})
        provenance=augment(source,output)
        raise_if_cancelled(run.id)
        # Permissions and labels may change while CPU extraction is running.
        validate_snapshot_current(json.loads(Path(snapshot.path).read_text()))
        parents=sorted((r for r in rows(LearningRun) if r.id!=run.id and r.status=='completed'),key=lambda r:r.created_at,reverse=True)
        record={'model':'Qwen3-VL-4B LoRA reviewed-video experiment',
            'data_relative_path':str(output.relative_to(folder())),
            'prepared_relative_path':str(source.relative_to(folder())),
            'records_sha256':provenance['records_sha256'],
            'steps':payload.steps,'learning_rate':payload.learning_rate,
            'resume_adapter':settings.verifier_adapter,
            'unique_training_videos':provenance['training_source_videos'],
            'training_examples':provenance['training_examples_including_variants'],
            'snapshot_counts':counts,'fresh_snapshot':True,'deployed':False,
            'summary':'Fresh approved dataset prepared; training queued. Live weights are unchanged.'}
        if parents:record['parent_run_id']=parents[0].id
        with record_lock:
            _patch(run.id,status='queued',record=record)
            update_workflow(run.id,'queued','queued','Dataset prepared; waiting for the GPU. Live weights are unchanged.',{'step':3,'total_steps':3})
            raise_if_cancelled(run.id)
            return get(LearningRun,run.id)
    except TrainingCancelled:
        mark_cancelled(run.id,'Dataset preparation cancelled; live weights are unchanged.')
        raise
    except Exception as error:
        with record_lock:
            # A simultaneous cancellation wins over an extraction failure.
            raise_if_cancelled(run.id)
            _patch(run.id,status='failed',record={'error':str(error),'summary':'Dataset preparation failed; live weights are unchanged.','deployed':False})
            update_workflow(run.id,'failed','failed',str(error))
        raise
    finally:
        current=get(LearningRun,run.id)
        if current and current.status in {'failed','cancelled','cancelling'}:
            for path in (source,output):
                if path.is_dir():shutil.rmtree(path)


# This opt-in policy schedules optimization only; promotion always remains manual.
_automation_claims=set()


def _automation_path():
    return folder()/'automation.json'


def get_automation_policy():
    with record_lock:
        path=_automation_path()
        policy={'enabled':False,'triggered_annotation_ids':[],'automatic_activation':False}
        if path.is_file():
            try:
                saved=json.loads(path.read_text())
                if (not isinstance(saved,dict) or type(saved.get('enabled')) is not bool
                    or not isinstance(saved.get('triggered_annotation_ids',[]),list)
                    or any(not isinstance(i,str) for i in saved.get('triggered_annotation_ids',[]))):
                    raise ValueError('Invalid automation policy')
                policy.update(saved)
            except (OSError,ValueError):
                policy.update(enabled=False,last_error='Saved automation policy is unreadable; automatic training is disabled.')
        policy['automatic_activation']=False
        return policy


def _write_automation_policy(policy):
    import os
    import tempfile
    path=_automation_path()
    descriptor,temporary=tempfile.mkstemp(prefix='.automation-',suffix='.json',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w') as stream:
            os.fchmod(stream.fileno(),0o600)
            json.dump(policy,stream,indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def set_automation_policy(enabled):
    from .db import now
    if type(enabled) is not bool:raise ValueError('enabled must be a boolean')
    with record_lock:
        policy=get_automation_policy()
        policy.update(enabled=enabled,updated_at=now(),automatic_activation=False)
        policy.pop('last_error',None)
        _write_automation_policy(policy)
        return policy


async def maybe_queue_after_annotation(asset,annotation):
    """Schedule one bounded run for a newly approved, permitted training example."""
    from . import learning
    from .db import now
    from .training_control import ACTIVE
    claimed=False
    try:
        with record_lock:
            policy=get_automation_policy()
            if not policy['enabled']:return {'queued':False,'reason':'disabled'}
            # Only the current human-approved visible label can trigger this.
            current=learning.asset_record(asset)
            if (annotation.status!='approved' or annotation.basis!='visible'
                or annotation.action=='insufficient_visibility' or not current['eligible']
                or current['split']!='train' or not current.get('annotation')
                or current['annotation']['id']!=annotation.id):
                return {'queued':False,'reason':'ineligible'}
            if (annotation.id in policy.get('triggered_annotation_ids',[])
                or any(r.record.get('trigger_annotation_id')==annotation.id for r in rows(LearningRun))):
                return {'queued':False,'reason':'already_triggered'}
            if _automation_claims or any(r.status in ACTIVE for r in rows(LearningRun)):
                return {'queued':False,'reason':'training_active'}
            _automation_claims.add(annotation.id);claimed=True
        result=await learning.queue_training(learning.TrainingRequest(steps=20))
        run=result['run']
        with record_lock:
            current_run=get(LearningRun,run.id)
            if current_run:
                current_run.record={**current_run.record,'trigger_annotation_id':annotation.id,
                    'trigger_asset_id':asset.id,'trigger_origin':'approved_annotation_automation','deployed':False}
                save(current_run)
            policy=get_automation_policy()
            policy['triggered_annotation_ids']=list(dict.fromkeys([*policy.get('triggered_annotation_ids',[]),annotation.id]))
            policy.update(last_trigger={'annotation_id':annotation.id,'asset_id':asset.id,'run_id':run.id,'timestamp':now()},automatic_activation=False)
            policy.pop('last_error',None)
            _write_automation_policy(policy)
        return {'queued':True,'run_id':run.id}
    except Exception as error:
        # The annotation API already returned. Persist a visible policy error
        # instead of leaving an unhandled background-task exception.
        with record_lock:
            try:
                policy=get_automation_policy()
                policy['last_error']=str(getattr(error,'detail',None) or error)[:500]
                _write_automation_policy(policy)
            except OSError:pass
        return {'queued':False,'reason':'preparation_failed'}
    finally:
        if claimed:
            with record_lock:_automation_claims.discard(annotation.id)
