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
from .video import digest, read_video, write_clip, decode, encode, Frame

gpu_busy=False

class ModelCommandError(RuntimeError):
    def __init__(self,message,metrics):super().__init__(message);self.metrics=metrics

async def command(argv,cwd=None):
    started=time.monotonic();start_time=now();peak=0
    process=await asyncio.create_subprocess_exec(*map(str,argv),cwd=cwd,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
    async def measure():
        nonlocal peak
        while process.returncode is None:
            probe=None
            try:
                probe=await asyncio.create_subprocess_exec('nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                output,_=await asyncio.wait_for(probe.communicate(),4)
                peak=max(peak,float(output.decode().strip().splitlines()[0]))
            except (OSError,ValueError,IndexError,asyncio.TimeoutError):pass
            finally:
                if probe and probe.returncode is None:
                    probe.kill();await probe.wait()
            await asyncio.sleep(.5)
    sampler=asyncio.create_task(measure())
    try:
        stdout,_=await asyncio.wait_for(process.communicate(),1800)
        metrics={'start_time':start_time,'end_time':now(),'runtime_seconds':time.monotonic()-started,
            'sampled_device_peak_vram_mb':peak,'exit_code':process.returncode,'device':'cuda:0',
            'memory_note':'Total device memory sampled at 0.5-second intervals, including any persistent CUDA context'}
        if process.returncode: raise ModelCommandError(f'{Path(str(argv[0])).name} failed: {stdout.decode(errors="replace")[-3000:]}',metrics)
        return metrics
    except BaseException:
        if process.returncode is None:
            os.killpg(process.pid,signal.SIGKILL); await process.wait()
        raise
    finally:
        sampler.cancel();await asyncio.gather(sampler,return_exceptions=True)

def export_trajectories(npz,out,source_frames=None):
    with np.load(npz,allow_pickle=False) as data:
        xyz=data['xyz']; t,h,w,_=xyz.shape
        step=max(1,int(np.sqrt(h*w/250)))
        xyz=xyz[:,::step,::step].reshape(t,-1,3)
        observed=data['observed'][:,::step,::step].reshape(t,-1) if 'observed' in data else None
        payload={'simulated':False,'label':'ESTIMATED 3D RECONSTRUCTION','units':'geometry-model units; not measured meters',
            'frame_indices':list(range(t)),'points':np.nan_to_num(xyz).round(4).tolist(),
            'confidence':observed.astype(float).tolist() if observed is not None else None,
            'confidence_note':'Observed-by-tracker mask (0 or 1), not calibrated 3D accuracy or likelihood of harm',
            'cameras':data['poses'].tolist() if 'poses' in data else None,
            'intrinsics':data['intrinsics'].tolist() if 'intrinsics' in data else None,
            'source_frames':source_frames[:t] if source_frames else None}
    out.write_text(json.dumps(payload))

async def run(job):
    incident=get(Incident,job.incident_id)
    if not incident: raise RuntimeError('Incident was removed')
    folder=(settings.data_dir/'incidents'/incident.id).resolve(); original=folder/'original.mp4'
    if digest(original)!=incident.record['original_sha256']: raise RuntimeError('Original hash mismatch')
    started=time.monotonic(); transforms=[]; files={}; executions=[];warnings=[]
    lockpath=Path('model-lock.json')
    provenance=json.loads(lockpath.read_text()).get(job.kind,{}) if lockpath.exists() else {}
    if job.kind=='relayvsr':
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
        await asyncio.to_thread(write_clip,list(read_video(output)),labeled,'AI-ENHANCED REVIEW COPY')
        files['review']=labeled.name
        transforms.append({'operation':'RelayVSR','output_sha256':digest(labeled)})
    elif job.kind=='relift':
        repo=Path('third_party/ReLift').resolve()
        for name,input_path in [('original',original),('enhanced',folder/incident.record['files'].get('enhanced','not-present'))]:
            if not input_path.is_file(): continue
            if name=='enhanced' and len(await asyncio.to_thread(lambda:list(read_video(input_path))))<2:
                warnings.append('Enhanced ReLift pass skipped: fewer than two enhanced frames');continue
            output=folder/f'relift-{name}-{job.id}'
            python = settings.relift_python or str(repo/'.venv/bin/python')
            try:
                executions.append(await command([python,'scripts/demo_video.py',input_path,'--out',output,'--num_frames',str(settings.relift_frame_limit)],repo))
            except Exception as error:
                if name=='original':raise
                warnings.append(f'Enhanced ReLift pass failed; original reconstruction retained: {error}')
                transforms.append({'operation':'ReLift','input':name,'status':'failed','error':str(error)});continue
            target=folder/f'trajectories-{name}-{job.id}.json'
            frame_metadata=incident.record.get('frame_timestamps',[]) if name=='original' else None
            await asyncio.to_thread(export_trajectories,output/'tracks.npz',target,frame_metadata)
            files[f'trajectory_{name}']=target.name
            transforms.append({'operation':'ReLift','input':name,'output_sha256':digest(target),'frame_limit':settings.relift_frame_limit})
    elif job.kind=='bvimamba':
        root=Path.cwd();output=folder/f'bvimamba-{job.id}.mp4'
        executions.append(await command([root/'.venv/bin/python','-m','scripts.bvimamba_video',original,output],root))
        files['bvimamba_review']=output.name
        transforms.append({'operation':'BVI-Mamba offline comparison','working_resolution':[512,512],
            'context_frames':5,'alignment':'centered; replicated boundaries','output_sha256':digest(output)})
    else: raise RuntimeError('Unknown job kind')
    incident=get(Incident,incident.id)
    incident.record={**incident.record,'files':{**incident.record['files'],**files},
        'transforms':incident.record['transforms']+transforms}
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
        if not sessions:
            jobs=sorted([j for j in rows(Job) if j.status=='queued'],key=lambda j:(j.priority,j.id))
            if jobs:
                gpu_busy=True
                job=jobs[0]; job.status='running'; job.attempts+=1; job.started_at=now(); save(job)
                await hub.publish('job',job.model_dump())
                try:
                    from ml.service import model_service
                    await model_service.stop()
                    from ml.verifier import release_verifier
                    await asyncio.to_thread(release_verifier)
                    job.result=await run(job); job.status='completed'; job.error=None
                except asyncio.CancelledError:
                    job.status='failed'; job.error='Worker interrupted'; raise
                except Exception as e:
                    job.status='failed'; job.error=str(e)
                    job.result={'status':'failed','error':str(e),**getattr(e,'metrics',{})}
                finally:
                    gpu_busy=False
                    job.ended_at=now(); save(job); await hub.publish('job',job.model_dump())
        await asyncio.sleep(1)

async def retention():
    from sqlmodel import Session, select
    import shutil
    while True:
        await asyncio.sleep(60)
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=settings.retention_hours)).isoformat()
        for incident in rows(Incident):
            if incident.created_at>=cutoff or any(j.incident_id==incident.id and j.status in ['queued','running'] for j in rows(Job)): continue
            folder=settings.data_dir/'incidents'/incident.id
            if folder.is_dir(): await asyncio.to_thread(shutil.rmtree,folder)
            incident.record={**incident.record,'expired':True,'files':{}}; save(incident)
        for source in rows(Source):
            if source.created_at>=cutoff or source.id in sessions or not source.path: continue
            if any(i.source_id==source.id and not i.record.get('expired') for i in rows(Incident)): continue
            path=Path(source.path)
            uploads=(settings.data_dir/'uploads').resolve()
            if path.resolve().parent==uploads:
                path.unlink(missing_ok=True)
                source.path=None; source.status='expired';save(source)
