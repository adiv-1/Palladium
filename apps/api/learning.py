"""Permissioned, append-only annotations and reproducible video datasets."""
import asyncio
import hashlib
import json
import os
import shutil
from datetime import datetime,timezone,timedelta
from pathlib import Path
from typing import Literal

import av
from fastapi import APIRouter,HTTPException,UploadFile,Form
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field as InputField,model_validator
from sqlalchemy import Column,JSON
from sqlmodel import SQLModel,Field
from .config import settings
from .db import uid,now,rows,get,save,Source,Incident
from .video import digest

router=APIRouter(prefix='/api/learning',tags=['reviewed learning'])
ACTIONS=['physical_contact','object_taking','ordinary_activity','insufficient_visibility']
PERMITTED={'permitted','academic_research'}

class LearningAsset(SQLModel,table=True):
    id:str=Field(default_factory=uid,primary_key=True)
    created_at:str=Field(default_factory=now)
    sha256:str=Field(index=True,unique=True)
    group_id:str=Field(index=True)
    title:str
    path:str
    duration:float
    origin:str
    origin_id:str|None=None
    expires_at:str
    provenance:dict=Field(default_factory=dict,sa_column=Column(JSON))

class LearningPermission(SQLModel,table=True):
    id:str=Field(default_factory=uid,primary_key=True)
    created_at:str=Field(default_factory=now)
    subject:str=Field(index=True)
    permission:str
    note:str

class LearningAnnotation(SQLModel,table=True):
    id:str=Field(default_factory=uid,primary_key=True)
    created_at:str=Field(default_factory=now)
    asset_id:str=Field(index=True)
    action:str
    start:float
    end:float
    status:str
    basis:str
    reviewer:str
    note:str=''
    description:str=''

class DatasetSnapshot(SQLModel,table=True):
    id:str=Field(default_factory=uid,primary_key=True)
    created_at:str=Field(default_factory=now)
    sha256:str
    path:str
    record:dict=Field(default_factory=dict,sa_column=Column(JSON))

class LearningRun(SQLModel,table=True):
    id:str=Field(default_factory=uid,primary_key=True)
    created_at:str=Field(default_factory=now)
    snapshot_id:str
    status:str='prepared'
    record:dict=Field(default_factory=dict,sa_column=Column(JSON))

class TrainingRequest(BaseModel):
    steps:int=InputField(80,ge=20,le=120)
    learning_rate:float=InputField(0.00001,gt=0,le=0.0001)

class AutomationInput(BaseModel):
    enabled:bool=InputField(strict=True)

class PermissionInput(BaseModel):
    permission:Literal['unknown','permitted','academic_research','denied']
    note:str=InputField(min_length=8,max_length=2000)

class AnnotationInput(BaseModel):
    action:Literal['physical_contact','object_taking','ordinary_activity','insufficient_visibility']
    start:float=InputField(ge=0,allow_inf_nan=False)
    end:float=InputField(gt=0,allow_inf_nan=False)
    basis:Literal['visible','external_context','uncertain']='visible'
    status:Literal['approved','pending','rejected']='approved'
    note:str=InputField('',max_length=2000)
    description:str=InputField('',max_length=140)
    @model_validator(mode='after')
    def interval(self):
        if self.end<=self.start:raise ValueError('End must follow start')
        if self.status=='approved' and (self.basis!='visible' or self.action=='insufficient_visibility'):
            raise ValueError('Training approval requires a visible, specific action or reviewed ordinary activity')
        if self.status=='approved' and self.action!='ordinary_activity':
            from .schemas import Observation
            Observation(timestamp_start=self.start,timestamp_end=self.end,actors=[],observation=self.description)
        return self

def folder():
    path=settings.data_dir/'learning';path.mkdir(exist_ok=True,parents=True);return path

def latest_permissions():
    result={}
    for item in sorted(rows(LearningPermission),key=lambda p:(p.created_at,p.id)):result[item.subject]=item
    return result

def permission_for(subject):
    item=latest_permissions().get(subject)
    return item.permission if item else 'unknown'

def latest_annotations():
    result={}
    for item in sorted(rows(LearningAnnotation),key=lambda p:(p.created_at,p.id)):result[item.asset_id]=item
    return result

def split_for(asset):
    supplied=asset.provenance.get('official_split')
    if supplied in ['train','val','test']:return supplied
    value=int(hashlib.sha256(asset.group_id.encode()).hexdigest()[:8],16)%100
    return 'train' if value<70 else 'val' if value<85 else 'test'

def register_local(path,title,group_id,origin,origin_id=None,provenance=None):
    path=Path(path);sha=digest(path)
    existing=next((a for a in rows(LearningAsset) if a.sha256==sha),None)
    if existing:return existing
    with av.open(str(path)) as container:
        if not container.streams.video:raise ValueError('A video track is required')
        stream=container.streams.video[0]
        duration=float(stream.duration*stream.time_base) if stream.duration is not None else (container.duration or 0)/av.time_base
    if not 0<duration<=3600:raise ValueError('Use a video between 0 and 3600 seconds')
    assets=folder()/'assets';assets.mkdir(exist_ok=True)
    suffix=path.suffix.lower()
    if suffix not in ['.mp4','.mov','.webm']:raise ValueError('Use MP4, MOV or WebM')
    target=assets/(sha+suffix)
    if not target.exists():
        with path.open('rb') as src,target.open('xb') as dest:shutil.copyfileobj(src,dest)
        target.chmod(0o444)
    return save(LearningAsset(sha256=sha,group_id=group_id,title=title[:160],path=str(target.resolve()),
        duration=duration,origin=origin,origin_id=origin_id,provenance=provenance or {},
        expires_at=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()))

def asset_record(asset,annotations=None,permissions=None):
    annotation=(annotations if annotations is not None else latest_annotations()).get(asset.id)
    grant=(permissions if permissions is not None else latest_permissions()).get('asset:'+asset.id)
    permission=grant.permission if grant else 'unknown'
    if asset.origin=='incident' and asset.provenance.get('source_id'):
        source_grant=(permissions if permissions is not None else latest_permissions()).get('source:'+asset.provenance['source_id'])
        if not source_grant or source_grant.permission not in PERMITTED:permission=source_grant.permission if source_grant else 'unknown'
        elif source_grant.permission=='academic_research' and permission=='permitted':permission='academic_research'
    available=Path(asset.path).is_file() and asset.expires_at>now()
    eligible=bool(available and permission in PERMITTED and annotation and annotation.status=='approved')
    return {k:v for k,v in {**asset.model_dump(),'path':None,'permission':permission,'eligible':eligible,
        'available':available,'split':split_for(asset),'annotation':annotation.model_dump() if annotation else None}.items() if k!='path'}

def retain_incident(incident):
    permission=permission_for('source:'+incident.source_id)
    if permission not in PERMITTED:return None
    source=get(Source,incident.source_id)
    path=settings.data_dir/'incidents'/incident.id/'original.mp4'
    if not path.is_file():raise ValueError('Original incident media is unavailable')
    if incident.simulated:raise ValueError('Simulated fixtures cannot become real training examples')
    asset=register_local(path,'Reviewed moment '+incident.id[:8],
        (source.original_sha256 if source and source.original_sha256 else incident.source_id),
        'incident',incident.id,{'source_id':incident.source_id,'source_sha256':incident.record.get('source_sha256'),
            'trigger_origin':incident.record.get('trigger_origin','model'),'model':incident.record.get('model')})
    if permission_for('asset:'+asset.id)=='unknown':
        save(LearningPermission(subject='asset:'+asset.id,permission=permission,note='Inherited from explicitly permitted source '+incident.source_id))
    return asset

def annotate(asset,payload,reviewer='workspace reviewer'):
    if payload.end>asset.duration+.05:raise ValueError('Label interval exceeds the video duration')
    return save(LearningAnnotation(asset_id=asset.id,reviewer=reviewer,**payload.model_dump()))

def build_snapshot():
    annotations=latest_annotations();permissions=latest_permissions();entries=[];groups={}
    for asset in sorted(rows(LearningAsset),key=lambda a:a.id):
        current_record=asset_record(asset,annotations,permissions)
        if not current_record['eligible']:continue
        if digest(asset.path)!=asset.sha256:raise ValueError('Asset hash mismatch: '+asset.id)
        label=annotations[asset.id];split=split_for(asset)
        if asset.group_id in groups and groups[asset.group_id]!=split:raise ValueError('Original recording appears in multiple dataset splits')
        groups[asset.group_id]=split
        entries.append({'asset_id':asset.id,'video_path':str(Path(asset.path).relative_to(settings.data_dir.resolve())),'video_sha256':asset.sha256,
            'group_id':asset.group_id,'split':split,'annotation':label.model_dump(),
            'permission':current_record['permission'],'provenance':asset.provenance})
    if not entries:raise ValueError('Approve visible labels on permitted videos before building a dataset')
    manifest={'version':1,'created_at':now(),'examples':entries,'training_automatic':False,
        'base_model':'Qwen/Qwen3-VL-4B-Instruct','task':'observable video actions; no legal guilt classification'}
    raw=json.dumps(manifest,sort_keys=True,indent=2).encode();sha=hashlib.sha256(raw).hexdigest()
    target=folder()/'snapshots';target.mkdir(exist_ok=True)
    path=target/(sha+'.json')
    with path.open('xb') as dest:dest.write(raw)
    path.chmod(0o444)
    counts={s:sum(e['split']==s for e in entries) for s in ['train','val','test']}
    return save(DatasetSnapshot(sha256=sha,path=str(path.resolve()),record={'counts':counts,'total':len(entries),'actions':sorted({e['annotation']['action'] for e in entries})}))

def validate_snapshot_current(manifest):
    annotations=latest_annotations();permissions=latest_permissions()
    for entry in manifest['examples']:
        asset=get(LearningAsset,entry['asset_id'])
        if not asset or not asset_record(asset,annotations,permissions)['eligible']:
            raise ValueError('Snapshot contains unavailable or no-longer-permitted footage; build a new dataset version')
        if asset_record(asset,annotations,permissions)['permission']!=entry['permission']:
            raise ValueError('Snapshot permissions changed; build a new dataset version')
        current=annotations.get(asset.id)
        if current is None or current.id!=entry['annotation']['id'] or asset.sha256!=entry['video_sha256']:
            raise ValueError('Snapshot annotations changed; build a new dataset version')
        if split_for(asset)!=entry['split']:raise ValueError('Snapshot split changed')

def expire_learning():
    protected=set()
    snapshots={s.id:s for s in rows(DatasetSnapshot)}
    for run in rows(LearningRun):
        if run.status in ['queued','running','preparing','cancelling'] and run.snapshot_id in snapshots:
            protected.update(e['asset_id'] for e in json.loads(Path(snapshots[run.snapshot_id].path).read_text())['examples'])
    for asset in rows(LearningAsset):
        if asset.expires_at<=now() and asset.id not in protected:
            path=Path(asset.path)
            # Only managed copies may be removed; never touch imported originals.
            if path.resolve().parent==(folder()/'assets').resolve():path.unlink(missing_ok=True)

@router.get('/assets')
def assets():
    annotations=latest_annotations();permissions=latest_permissions()
    return [asset_record(a,annotations,permissions) for a in sorted(rows(LearningAsset),key=lambda a:a.created_at,reverse=True)]

@router.get('/summary')
def summary():
    from .training_control import live_return
    from .learning_pipeline import get_automation_policy
    data=assets()
    return {'total':len(data),'eligible':sum(a['eligible'] for a in data),'pending':sum(not a['annotation'] or a['annotation']['status']=='pending' for a in data),
        'automatic_training':get_automation_policy()['enabled'],'automatic_activation':False,'workflow_control':True,'live_return':dict(live_return),'retention_days':30,'snapshots':rows(DatasetSnapshot),'runs':rows(LearningRun)}

@router.post('/sources/{source_id}/permission')
def source_permission(source_id:str,payload:PermissionInput):
    if not get(Source,source_id):raise HTTPException(404,'Source not found')
    return save(LearningPermission(subject='source:'+source_id,**payload.model_dump()))

@router.get('/sources/{source_id}/permission')
def source_permission_get(source_id:str):return {'permission':permission_for('source:'+source_id)}

@router.post('/assets/{asset_id}/permission')
def asset_permission(asset_id:str,payload:PermissionInput):
    if not get(LearningAsset,asset_id):raise HTTPException(404)
    return save(LearningPermission(subject='asset:'+asset_id,**payload.model_dump()))

@router.post('/incidents/{incident_id}/retain')
def retain(incident_id:str):
    incident=get(Incident,incident_id)
    if not incident:raise HTTPException(404)
    if incident.review_status!='confirmed':raise HTTPException(409,'Confirm the event before retaining it for learning')
    try:
        asset=retain_incident(incident)
        if not asset:raise HTTPException(409,'Training reuse is not permitted for this source')
        return asset_record(asset)
    except ValueError as e:raise HTTPException(409,str(e))

@router.get('/automation')
def automation_policy():
    from .learning_pipeline import get_automation_policy
    return get_automation_policy()

@router.post('/automation')
def automation_policy_set(payload:AutomationInput):
    from .learning_pipeline import set_automation_policy
    return set_automation_policy(payload.enabled)

@router.post('/assets/{asset_id}/annotations')
async def annotation_create(asset_id:str,payload:AnnotationInput):
    asset=get(LearningAsset,asset_id)
    if not asset:raise HTTPException(404)
    try:result=annotate(asset,payload)
    except ValueError as e:raise HTTPException(422,str(e))
    from .learning_pipeline import maybe_queue_after_annotation
    from .main import background
    background(maybe_queue_after_annotation(asset,result))
    return result

@router.get('/assets/{asset_id}/annotations')
def annotation_history(asset_id:str):return [a for a in rows(LearningAnnotation) if a.asset_id==asset_id]

@router.get('/assets/{asset_id}/media')
def media(asset_id:str):
    asset=get(LearningAsset,asset_id)
    if not asset or not asset_record(asset)['available']:raise HTTPException(404,'Learning video unavailable or expired')
    return FileResponse(asset.path)

@router.post('/upload')
async def upload(file:UploadFile,title:str=Form('Uploaded learning video')):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in ['.mp4','.mov','.webm']:raise HTTPException(400,'Use MP4, MOV or WebM')
    temporary=folder()/(uid()+suffix);size=0
    try:
        with temporary.open('xb') as dest:
            while chunk:=await file.read(1024**2):
                size+=len(chunk)
                if size>settings.max_upload_mb*1024**2:raise HTTPException(413,'Video too large')
                dest.write(chunk)
        asset=await asyncio.to_thread(register_local,temporary,title,digest(temporary),'upload')
        return asset_record(asset)
    except (ValueError,av.error.FFmpegError) as e:raise HTTPException(400,str(e))
    finally:temporary.unlink(missing_ok=True);await file.close()

@router.post('/snapshots')
def snapshot_create():
    try:return build_snapshot()
    except ValueError as e:raise HTTPException(409,str(e))

@router.get('/snapshots/{snapshot_id}/manifest')
def snapshot_manifest(snapshot_id:str):
    snapshot=get(DatasetSnapshot,snapshot_id)
    if not snapshot:raise HTTPException(404)
    return FileResponse(snapshot.path,media_type='application/json',filename='dataset-'+snapshot.id+'.json')

def run_examples(run_id):
    run=get(LearningRun,run_id)
    if not run:raise HTTPException(404,'Experiment not found')
    relative=run.record.get('data_relative_path')
    if not relative:raise HTTPException(404,'This experiment has no retained frame bundle')
    root=(folder()/relative).resolve()
    if folder().resolve() not in root.parents:raise HTTPException(500,'Invalid experiment storage path')
    path=root/'records.jsonl'
    if digest(path)!=run.record.get('records_sha256'):raise HTTPException(409,'Experiment bundle hash changed')
    return root,[json.loads(line) for line in path.read_text().splitlines()]

@router.post('/train')
async def queue_training(payload:TrainingRequest):
    from .db import Job
    from .training_control import ACTIVE, TrainingCancelled, raise_if_cancelled, update_workflow, record_lock
    if any(r.status in ACTIVE for r in rows(LearningRun)):
        raise HTTPException(409,'A training run is already queued or running')
    try:
        from .learning_pipeline import prepare_training_run
        run=await asyncio.to_thread(prepare_training_run,payload)
        with record_lock:
            raise_if_cancelled(run.id)
            run=get(LearningRun,run.id)
            run.status='queued';save(run)
            update_workflow(run.id,'queued','queued','Reviewed dataset prepared; waiting for the GPU to become idle.')
            job=save(Job(incident_id=run.id,kind='learning_train',priority=1))
        return {'run':get(LearningRun,run.id),'job':job}
    except TrainingCancelled:
        raise HTTPException(409,'Training preparation was cancelled; live weights are unchanged')
    except ValueError as error:
        raise HTTPException(409,str(error))


@router.post('/runs/{run_id}/cancel')
async def cancel_training(run_id:str):
    from .training_control import cancel_run
    try:run,requested=await cancel_run(run_id)
    except ValueError as error:raise HTTPException(404,str(error))
    return {'run':run,'cancellation_requested':requested}


@router.post('/return-to-live')
async def request_return_to_live():
    from .training_control import return_to_live
    try:return await return_to_live()
    except TimeoutError as error:raise HTTPException(409,str(error))


async def run_training_job(job):
    from .jobs import command
    from .training_control import (TrainingCancelled,register,unregister,raise_if_cancelled,
        mark_cancelled,update_workflow,progress_reader)
    run=get(LearningRun,job.incident_id)
    if not run:raise ValueError('Training run does not exist')
    cancel_event=register(run.id)
    try:
        raise_if_cancelled(run.id)
        run.status='running';save(run)
        update_workflow(run.id,'preparing','running','Checking current permissions, reviewed labels, immutable dataset and source hashes.')
        snapshot=get(DatasetSnapshot,run.snapshot_id)
        validate_snapshot_current(json.loads(Path(snapshot.path).read_text()))
        root,_=run_examples(run.id);output=folder()/'candidates'/run.id
        raise_if_cancelled(run.id)
        update_workflow(run.id,'preparing','completed','Current permissions and reviewed dataset provenance verified.')
        update_workflow(run.id,'loading','running','Loading the pinned verifier and candidate adapter in an isolated subprocess.')
        execution=await command([Path.cwd()/'.venv/bin/python','-m','scripts.train_learning',
            '--data',root,'--checkpoint',settings.verifier_checkpoint,'--out',output,
            '--resume-adapter',run.record['resume_adapter'],'--steps',str(run.record['steps']),
            '--learning-rate',str(run.record['learning_rate'])],
            cancel_event=cancel_event,progress_reader=progress_reader(run.id,output/'progress.json'))
        raise_if_cancelled(run.id)
        report=json.loads((output/'evaluation.json').read_text())
        update_workflow(run.id,'evaluating','completed','Held-out candidate evaluation and comparison finished.')
        registry={'status':'not_configured'}
        if os.environ.get('HF_ADAPTER_REPO'):
            update_workflow(run.id,'publishing','running','Publishing a versioned private candidate adapter; live model remains unchanged.')
            try:
                await command([Path.cwd()/'.venv/bin/python','-m','scripts.publish_adapter',
                    '--adapter',output/'adapter','--version','candidate-'+run.id,
                    '--selection','candidate','--output',output/'registry.json'],cancel_event=cancel_event)
                registry=json.loads((output/'registry.json').read_text())
                update_workflow(run.id,'publishing','completed','Candidate registry publication finished; no live activation was performed.')
            except TrainingCancelled:raise
            except Exception:
                registry={'status':'failed','error':'Adapter publication failed; the evaluated candidate remains stored locally.'}
                update_workflow(run.id,'publishing','failed',registry['error'])
        raise_if_cancelled(run.id)
        run=get(LearningRun,run.id)
        run.status='completed';run.record={**run.record,'evaluation':report,'registry':registry,
            'candidate_relative_path':str(output.relative_to(folder())),'deployed':False,
            'summary':f"Trained {report['training_examples']} variants from {report['unique_training_videos']} source videos. Non-regression: {report['nonregression']}. Candidate is not automatically deployed."}
        save(run)
        update_workflow(run.id,'completed','completed','Training and held-out evaluation finished. Selected live model was not changed.')
        return {'run_id':run.id,'status':'completed','execution':execution,
            'adapter_sha256':report['adapter_sha256'],'nonregression':report['nonregression'],
            'improved_over_parent':report['improved_over_parent'],'deployed':False}
    except TrainingCancelled as error:
        mark_cancelled(run.id,str(error));raise
    except BaseException as error:
        latest=get(LearningRun,run.id)
        if latest and latest.record.get('workflow',{}).get('cancel_requested'):
            mark_cancelled(run.id,'Training cancelled; subprocess was stopped')
            raise TrainingCancelled('Training cancelled') from error
        if latest:
            latest.status='failed';latest.record={**latest.record,'error':str(error) or 'Training interrupted','deployed':False};save(latest)
            update_workflow(run.id,'failed','failed',str(error) or 'Training interrupted')
        raise
    finally:
        unregister(run.id)

@router.get('/runs/{run_id}/examples')
def examples(run_id:str):
    root,records=run_examples(run_id)
    return [{k:v for k,v in r.items() if k not in ['frames']} for r in records]

@router.get('/runs/{run_id}/examples/{asset_id}/frames/{index}')
def training_frame(run_id:str,asset_id:str,index:int):
    root,records=run_examples(run_id)
    record=next((r for r in records if r['id']==asset_id),None)
    if record is None or index<0 or index>=len(record['frames']):raise HTTPException(404)
    path=(root/record['frames'][index]).resolve()
    if root not in path.parents or digest(path)!=record['frame_sha256'][index]:raise HTTPException(409,'Frame provenance mismatch')
    return FileResponse(path,media_type='image/jpeg')
