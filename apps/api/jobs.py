import asyncio
import json
import os
import signal
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np
from .config import settings
from .db import Job, Incident, rows, get, save, now, engine, Source
from .runtime import sessions, hub
from .training_control import TrainingCancelled
from .video import digest, read_video, write_clip, decode, encode, Frame, align_frame_timing

gpu_busy=False
live_warmup=False
live_idle_until=0.0

def preserve_live_readiness():
    """Allow the next recording to begin before idle GPU work evicts live models."""
    global live_idle_until
    live_idle_until=max(live_idle_until,time.monotonic()+settings.live_idle_grace_seconds)

def background_ready(job, at=None):
    # CPU exports remain independent of the live GPU and its warmup/grace period.
    if job.kind=='relift_export':return True
    return not sessions and not gpu_busy and not live_warmup and (time.monotonic() if at is None else at)>=live_idle_until

class ModelCommandError(RuntimeError):
    def __init__(self,message,metrics):super().__init__(message);self.metrics=metrics

async def terminate_process_group(process,grace=3):
    """Terminate the dedicated subprocess group, including surviving children."""
    if getattr(process,'_safesc_group_stopped',False):return
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    waiter=asyncio.create_task(process.wait())
    try:await asyncio.wait_for(asyncio.shield(waiter),grace)
    except asyncio.TimeoutError:pass
    # A parent can exit before its CUDA-owning children; kill the group as well.
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    await waiter
    process._safesc_group_stopped=True

async def command(argv,cwd=None,*,cancel_event=None,progress_reader=None):
    from .training_control import TrainingCancelled
    started=time.monotonic();start_time=now();peak=0
    if cancel_event and cancel_event.is_set():raise TrainingCancelled('Training cancelled before process launch')
    process=await asyncio.create_subprocess_exec(*map(str,argv),cwd=cwd,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
    tail=bytearray()
    async def consume():
        while chunk:=await process.stdout.read(16384):
            tail.extend(chunk)
            if len(tail)>65536:del tail[:-65536]
        await process.wait()
    async def monitor():
        nonlocal peak
        last_measure=0
        while not output.done():
            if cancel_event and cancel_event.is_set():
                await terminate_process_group(process)
                raise TrainingCancelled('Training cancelled; subprocess and children were stopped')
            if progress_reader:progress_reader()
            if time.monotonic()-last_measure>=.5:
                probe=None
                try:
                    probe=await asyncio.create_subprocess_exec('nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                    gpu_output,_=await asyncio.wait_for(probe.communicate(),1)
                    peak=max(peak,float(gpu_output.decode().strip().splitlines()[0]))
                except (OSError,ValueError,IndexError,asyncio.TimeoutError):pass
                finally:
                    if probe and probe.returncode is None:probe.kill();await probe.wait()
                last_measure=time.monotonic()
            await asyncio.sleep(.1)
    output=asyncio.create_task(consume());watch=asyncio.create_task(monitor())
    try:
        done,_=await asyncio.wait([output,watch],timeout=1800,return_when=asyncio.FIRST_COMPLETED)
        if not done:raise asyncio.TimeoutError('Model subprocess exceeded 30 minutes')
        if watch in done:watch.result()
        await output
        if cancel_event and cancel_event.is_set():raise TrainingCancelled('Training cancelled before result publication')
        if progress_reader:progress_reader()
        metrics={'start_time':start_time,'end_time':now(),'runtime_seconds':time.monotonic()-started,
            'sampled_device_peak_vram_mb':peak,'exit_code':process.returncode,'device':'cuda:0',
            'memory_note':'Total device memory sampled during subprocess execution'}
        if process.returncode:raise ModelCommandError(f'{Path(str(argv[0])).name} failed: {tail.decode(errors="replace")[-3000:]}',metrics)
        return metrics
    except BaseException:
        await terminate_process_group(process)
        raise
    finally:
        watch.cancel();output.cancel()
        await asyncio.gather(watch,output,return_exceptions=True)

from .trajectories import export_trajectories

async def run(job):
    if job.kind=='learning_train':
        from .learning import run_training_job
        return await run_training_job(job)
    incident=get(Incident,job.incident_id)
    if not incident: raise RuntimeError('Incident was removed')
    folder=(settings.data_dir/'incidents'/incident.id).resolve(); original=folder/'original.mp4'
    if digest(original)!=incident.record['original_sha256']: raise RuntimeError('Original hash mismatch')
    started=time.monotonic(); transforms=[]; files={}; executions=[];warnings=[];assessments={}
    lockpath=Path('model-lock.json')
    provenance=json.loads(lockpath.read_text()).get(job.kind,{}) if lockpath.exists() else {}
    if job.kind=='review_brief':
        initial=folder/f'initial-review-{job.id}.json'
        initial.write_text(json.dumps({'origin':incident.record.get('trigger_origin','model'),
            'observations':[e.get('observation','') for e in incident.record.get('observable_events',[])[:2]],
            'source_time_offset':incident.record.get('clip_offset',0)}))
        output=folder/f'clip-review-{job.id}.json'
        executions.append(await command([Path.cwd()/'.venv/bin/python','-m','scripts.review_incident',original,output,'--initial',initial]))
        report=json.loads(output.read_text())
        if report['input_sha256']!=digest(original):raise RuntimeError('Clip review input hash mismatch')
        incident=get(Incident,incident.id);incident.record={**incident.record,'clip_review':report};save(incident)
        files['clip_review']=output.name
        transforms.append({'operation':'Secondary original-clip review','model':report['model'],
            'revision':report['revision'],'research_adapter_used':False,'output_sha256':digest(output)})
    elif job.kind=='relayvsr':
        repo=Path('third_party/RelayVSR').resolve(); output=folder/f'relayvsr-{job.id}.mp4'
        working=original
        def working_copy():
            import cv2
            frames=list(read_video(original));image=decode(frames[0].jpeg);h,w=image.shape[:2]
            if max(w,h)<=settings.relay_max_input_dimension:return original,None
            ratio=settings.relay_max_input_dimension/max(w,h)
            width=max(2,round(w*ratio/2)*2);height=max(2,round(h*ratio/2)*2)
            resized=[Frame(f.timestamp,f.index,encode(cv2.resize(decode(f.jpeg),(width,height),interpolation=cv2.INTER_AREA))) for f in frames]
            path=folder/f'relay-working-{job.id}.mp4';write_clip(resized,path)
            return path,{'operation':'RelayVSR working-copy resize','original_dimensions':[w,h],
                'working_dimensions':[width,height],'output_scale':4,'timestamps_preserved':True,
                'source_sha256':digest(original),'working_sha256':digest(path)}
        working,resize=await asyncio.to_thread(working_copy)
        if resize:transforms.append(resize)
        executions.append(await command([repo/'.venv/bin/python','inference.py','--input',working,'--output',output],repo))
        labeled=folder/f'review-{job.id}.mp4'
        def label_with_source_timing():
            source_frames=list(read_video(working));generated=list(read_video(output))
            if len(source_frames)!=len(generated):
                raise RuntimeError('Enhancement frame count differs from input; original retained without inventing frame alignment')
            aligned=align_frame_timing(source_frames,generated)
            write_clip(aligned,labeled,'AI-ENHANCED REVIEW COPY')
            return {'operation':'Preserve original frame timing on enhanced copy','frames':len(aligned),
                'duration_seconds':aligned[-1].timestamp-aligned[0].timestamp,'interpolated_frames':False}
        transforms.append(await asyncio.to_thread(label_with_source_timing))
        files['review']=labeled.name
        transforms.append({'operation':'RelayVSR','output_sha256':digest(labeled)})
    elif job.kind=='relift_export':
        candidates=sorted(folder.glob('relift-original-*/tracks.npz'),key=lambda p:p.stat().st_mtime,reverse=True)
        if not candidates:raise RuntimeError('No cached ReLift result; run the full reconstruction job first')
        npz=candidates[0];mapping=npz.parent/'input-map.json'
        source_frames=json.loads(mapping.read_text()) if mapping.exists() else incident.record.get('frame_timestamps')
        target=folder/f'trajectories-original-{job.id}.json';motion=folder/f'motion-review-{job.id}.mp4'
        quality=await asyncio.to_thread(export_trajectories,npz,target,source_frames,motion)
        files.update(trajectory_original=target.name,motion_review=motion.name)
        assessments['trajectory_original']={'status':'estimated','export_version':2,**quality}
        transforms.append({'operation':'ReLift colored export and video reprojection','inference_rerun':False,
            'raw_tracks_sha256':await asyncio.to_thread(digest,npz),'output_sha256':digest(target)})
    elif job.kind=='relift':
        repo=Path('third_party/ReLift').resolve()
        for name,input_path in [('original',original),('enhanced',folder/incident.record['files'].get('enhanced','not-present'))]:
            if not input_path.is_file(): continue
            if name=='enhanced' and len(await asyncio.to_thread(lambda:list(read_video(input_path))))<2:
                warnings.append('Enhanced ReLift pass skipped: fewer than two enhanced frames');continue
            output=folder/f'relift-{name}-{job.id}'
            # Sample the full incident interval, not only its first 84 frames.
            def prepare_relift_input():
                frames=list(read_video(input_path))
                indices=np.linspace(0,len(frames)-1,min(len(frames),settings.relift_frame_limit)).round().astype(int)
                chosen=[frames[i] for i in indices]
                mapping=[(incident.record.get('native_frame_timestamps') or incident.record['frame_timestamps'])[i] for i in indices] if name=='original' else [{'index':f.index,'timestamp':f.timestamp+incident.record.get('clip_offset',0)+incident.record.get('artifact_time_offsets',{}).get('enhanced',0)} for f in chosen]
                working=folder/f'relift-input-{name}-{job.id}.mp4';write_clip(chosen,working)
                output.mkdir(exist_ok=True);(output/'input-map.json').write_text(json.dumps(mapping))
                return working,mapping
            working,frame_metadata=await asyncio.to_thread(prepare_relift_input)
            python = settings.relift_python or str(repo/'.venv/bin/python')
            try:
                executions.append(await command([python,'scripts/demo_video.py',working,'--out',output,'--num_frames',str(settings.relift_frame_limit)],repo))
            except Exception as error:
                if name=='original':raise
                warnings.append(f'Enhanced ReLift pass failed; original reconstruction retained: {error}')
                transforms.append({'operation':'ReLift','input':name,'status':'failed','error':str(error)});continue
            target=folder/f'trajectories-{name}-{job.id}.json'
            motion=folder/f'motion-{name}-{job.id}.mp4'
            quality=await asyncio.to_thread(export_trajectories,output/'tracks.npz',target,frame_metadata,motion)
            if name=='original':files['motion_review']=motion.name
            assessments[f'trajectory_{name}']={'status':'estimated','export_version':2,**quality}
            files[f'trajectory_{name}']=target.name
            transforms.append({'operation':'ReLift','input':name,'output_sha256':digest(target),'frame_limit':settings.relift_frame_limit})
    elif job.kind=='bvimamba':
        root=Path.cwd();output=folder/f'bvimamba-{job.id}.mp4'
        executions.append(await command([root/'.venv/bin/python','-m','scripts.bvimamba_video',original,output],root))
        files['bvimamba_review']=output.name
        assessment=json.loads(Path(str(output)+'.json').read_text())
        assessments['bvimamba_review']={k:v for k,v in assessment.items() if k!='frame_metrics'}
        transforms.append({'operation':'BVI-Mamba offline comparison','working_resolution':[512,512],
            'context_frames':5,'alignment':'centered; replicated boundaries','output_sha256':digest(output)})
    else: raise RuntimeError('Unknown job kind')
    incident=get(Incident,incident.id)
    history=list(incident.record.get('artifact_history',[]))
    for kind,filename in files.items():
        old=incident.record['files'].get(kind)
        if old and old!=filename:history.append({'kind':kind,'filename':old,'superseded_by':filename,'timestamp':now()})
    incident.record={**incident.record,'files':{**incident.record['files'],**files},
        'artifact_assessments':{**incident.record.get('artifact_assessments',{}),**assessments},
        'artifact_history':history,'transforms':incident.record['transforms']+transforms}
    save(incident)
    return {'status':'ok','model':job.kind,'revision':provenance.get('source_revision','unconfigured'),
        'model_provenance':provenance,'executions':executions,
        'warnings':warnings,
        'runtime_seconds':time.monotonic()-started,'files':files,'source_sha256':digest(original),
        'output_hashes':{kind:digest(folder/name) for kind,name in files.items()}}

async def worker():
    global gpu_busy
    while True:
        # Heavy jobs wait until no active ingestion/model session. Never evict a live detector midstream.
        jobs=sorted([j for j in rows(Job) if j.status=='queued' and background_ready(j)],key=lambda j:(j.priority,j.id))
        if jobs:
            job=jobs[0];requires_gpu=job.kind!='relift_export'
            if requires_gpu:gpu_busy=True
            job.status='running'; job.attempts+=1; job.started_at=now(); save(job)
            await hub.publish('job',job.model_dump())
            try:
                if requires_gpu:
                    from ml.service import model_service
                    await model_service.stop()
                    from ml.verifier import release_verifier
                    await asyncio.to_thread(release_verifier)
                job.result=await run(job); job.status='completed'; job.error=None
            except TrainingCancelled as e:
                job.status='cancelled';job.error=None;job.result={'status':'cancelled','reason':str(e),'deployed':False}
            except asyncio.CancelledError:
                job.status='failed'; job.error='Worker interrupted'; raise
            except Exception as e:
                job.status='failed'; job.error=str(e)
                job.result={'status':'failed','error':str(e),**getattr(e,'metrics',{})}
            finally:
                if requires_gpu:gpu_busy=False
                job.ended_at=now(); save(job); await hub.publish('job',job.model_dump())
        await asyncio.sleep(1)

def expire_local():
    """One cleanup pass, serialized with retention-hold changes and archiving."""
    import shutil
    from .retention import retention_lock, held
    from .archive import enabled as archive_enabled
    from .db import ArchiveState
    with retention_lock:
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=settings.retention_hours)).isoformat()
        active_jobs={j.incident_id for j in rows(Job) if j.status in ['queued','running']}
        for incident in rows(Incident):
            if held(incident) or incident.created_at>=cutoff or incident.id in active_jobs:continue
            state=get(ArchiveState,incident.id)
            if archive_enabled() and (not state or state.status not in ['synced','evicted','expired']):continue
            folder=settings.data_dir/'incidents'/incident.id
            if folder.is_dir():shutil.rmtree(folder)
            incident.record={**incident.record,'expired':True,'files':{}};save(incident)
        for source in rows(Source):
            if source.created_at>=cutoff or source.id in sessions or not source.path:continue
            if any(i.source_id==source.id and (held(i) or not i.record.get('expired')) for i in rows(Incident)):continue
            path=Path(source.path)
            if path.resolve().parent==(settings.data_dir/'uploads').resolve():
                path.unlink(missing_ok=True)
                source.path=None;source.status='expired';save(source)


async def retention():
    while True:
        await asyncio.sleep(60)
        from .learning import expire_learning
        await asyncio.to_thread(expire_learning)
        await asyncio.to_thread(expire_local)
