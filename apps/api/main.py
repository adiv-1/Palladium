import asyncio
import hmac
import json
import math
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .config import settings
from .db import init, Source, Incident, Job, ReviewAction, rows, get, save
from .schemas import SourceRequest, Review
from .video import Frame, digest, decode
from .runtime import hub, sessions, pairings, VideoSession, replay
from .jobs import worker, retention

tasks=set()
upload_in_progress=False
def busy():
    from . import jobs
    return bool(sessions) or jobs.gpu_busy or upload_in_progress
def background(coro):
    task=asyncio.create_task(coro); tasks.add(task); task.add_done_callback(tasks.discard); return task

@asynccontextmanager
async def lifespan(app):
    init(); background(worker()); background(retention())
    if theft.enabled(): background(theft.poller())
    yield
    for task in list(tasks): task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    for session in list(sessions.values()): await session.close()
    from ml.service import model_service
    from ml.verifier import release_verifier
    await model_service.stop()
    await asyncio.to_thread(release_verifier)

app=FastAPI(title='SafeSC',lifespan=lifespan)
def authenticated(request: Request):
    token=request.cookies.get('safesc_token') or request.headers.get('Authorization','').removeprefix('Bearer ')
    if not token or not hmac.compare_digest(token,settings.app_token): raise HTTPException(401,'Sign in to SafeSC')

from .activity import router as activity_router
app.include_router(activity_router, dependencies=[Depends(authenticated)])
from . import theft
app.include_router(theft.router, dependencies=[Depends(authenticated)])
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.post('/api/login')
async def login(request:Request,response:Response):
    payload=await request.json()
    if not hmac.compare_digest(str(payload.get('token','')),settings.app_token): raise HTTPException(401,'Invalid access token')
    response.set_cookie('safesc_token',settings.app_token,httponly=True,secure=request.url.scheme=='https',samesite='strict',max_age=86400)
    return {'ok':True}

@app.get('/api/health',dependencies=[Depends(authenticated)])
async def health():
    gpu=[]
    try:
        proc=await asyncio.create_subprocess_exec('nvidia-smi','--query-gpu=name,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits',stdout=asyncio.subprocess.PIPE)
        out,_=await asyncio.wait_for(proc.communicate(),3); gpu=out.decode().strip().splitlines()
    except (OSError,asyncio.TimeoutError): pass
    from ml.service import model_service
    from ml.verifier import verifier_is_loaded
    return {'status':'ok','streaming_model':settings.streaming_model,'revision':settings.model_revision,
        'simulated':settings.streaming_model=='fixture','gpu':gpu,'active_sources':len(sessions),
        'enhancement_model':settings.enhancement_model,'postprocessing_waits_for_idle':True,
        'moss_process_running':bool(model_service.process and model_service.process.returncode is None),
        'visual_verifier':settings.observation_verifier_model,'visual_verifier_loaded':verifier_is_loaded(),
        'tracking_enabled':settings.enable_tracking,'relayvsr_enabled':settings.enable_relay_vsr,
        'relift_enabled':settings.enable_relift,'bvimamba_enabled':settings.enable_bvimamba,
        'retention_hours':settings.retention_hours,'theft_worker':theft.status()}

@app.post('/api/models/moss/warm',dependencies=[Depends(authenticated)])
async def warm_moss():
    if busy(): raise HTTPException(409,'Stop the current stream or wait for postprocessing')
    from ml.service import model_service
    try:
        await model_service.ensure()
        if settings.observation_verifier_model=='qwen3vl':
            from ml.verifier import acquire_verifier
            await asyncio.to_thread(acquire_verifier,settings.verifier_checkpoint)
        import httpx
        url=settings.moss_ws_url.replace('ws://','http://').replace('wss://','https://').split('/v1/')[0]+'/health'
        async with httpx.AsyncClient(timeout=3) as client:
            response=await client.get(url)
            response.raise_for_status()
            if not response.json().get('model_loaded'):raise RuntimeError('MOSS is not loaded')
    except Exception as e:
        raise HTTPException(503,str(e))
    return {'status':'ready','model':'moss','revision':settings.model_revision}

@app.get('/api/sources',dependencies=[Depends(authenticated)])
def sources(): return sorted(rows(Source),key=lambda source:source.created_at,reverse=True)

@app.post('/api/sources/{source_id}/bookmark',dependencies=[Depends(authenticated)])
async def bookmark(source_id:str):
    session=sessions.get(source_id)
    if not session:raise HTTPException(409,'Connect a source before marking a moment')
    try:return session.bookmark()
    except ValueError as error:raise HTTPException(409,str(error))

@app.get('/api/reviews/export',dependencies=[Depends(authenticated)])
def export_reviews():
    incidents={i.id:i for i in rows(Incident)}
    return {'kind':'human-review-feedback','automatic_training':False,
        'note':'Reviewer judgments are not independent ground truth. Evaluate proposed changes on a held-out labeled set.',
        'reviews':[{**r.model_dump(),'model_provenance':incidents[r.incident_id].record.get('model',{}),
            'observation_model':incidents[r.incident_id].record.get('observation_model',{}),
            'source_sha256':incidents[r.incident_id].record.get('source_sha256'),
            'trigger_origin':incidents[r.incident_id].record.get('trigger_origin','model'),
            'simulated':incidents[r.incident_id].simulated} for r in rows(ReviewAction) if r.incident_id in incidents]}

@app.post('/api/sources',dependencies=[Depends(authenticated)])
async def source_create(payload:SourceRequest):
    if payload.kind=='rtsp' and not settings.allow_rtsp: raise HTTPException(400,'RTSP must be explicitly enabled by the operator')
    if payload.kind=='rtsp' and (not payload.url or not payload.url.startswith(('rtsp://','rtsps://'))): raise HTTPException(400,'Expected RTSP URL')
    if sessions or upload_in_progress: raise HTTPException(409,'Stop the active source before connecting another')
    source=save(Source(name=payload.name,kind=payload.kind))
    if payload.kind=='rtsp': background(replay(source,payload.url))
    token=secrets.token_urlsafe(32); pairings[token]={'source_id':source.id,'expires':time.time()+3600}
    return {'source':source,'capture_url':f'{settings.public_url}/capture#{token}'}

@app.post('/api/upload',dependencies=[Depends(authenticated)])
async def upload(file:UploadFile):
    global upload_in_progress
    if sessions or upload_in_progress: raise HTTPException(409,'Stop the active source before uploading another')
    if Path(file.filename or '').suffix.lower() not in ['.mp4','.mov','.webm']: raise HTTPException(400,'Use MP4, MOV or WebM')
    source=Source(name=Path(file.filename).name,kind='upload')
    folder=settings.data_dir/'uploads'; folder.mkdir(exist_ok=True)
    path=folder/f'{source.id}{Path(file.filename).suffix.lower()}'
    total=0; upload_in_progress=True
    try:
        with path.open('xb') as out:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>settings.max_upload_mb*1024**2: raise HTTPException(413,'Video exceeds upload size limit')
                out.write(chunk)
        if not total: raise HTTPException(400,'Empty upload')
        path.chmod(0o444); source.path=str(path); source.original_sha256=await asyncio.to_thread(digest,path)
        save(source); background(replay(source,path)); return source
    except BaseException:
        path.unlink(missing_ok=True); raise
    finally:
        upload_in_progress=False
        await file.close()

@app.get('/api/incidents',dependencies=[Depends(authenticated)])
def incidents(): return sorted(rows(Incident),key=lambda i:i.created_at,reverse=True)

@app.get('/api/incidents/{incident_id}',dependencies=[Depends(authenticated)])
def incident_detail(incident_id:str):
    incident=get(Incident,incident_id)
    if not incident: raise HTTPException(404)
    return {'incident':incident,'jobs':[j for j in rows(Job) if j.incident_id==incident_id],
        'history':[r for r in rows(ReviewAction) if r.incident_id==incident_id]}

@app.post('/api/incidents/{incident_id}/review',dependencies=[Depends(authenticated)])
async def review(incident_id:str,payload:Review):
    incident=get(Incident,incident_id)
    if not incident: raise HTTPException(404)
    save(ReviewAction(incident_id=incident_id,decision=payload.decision,note=payload.note))
    incident.review_status=payload.decision; save(incident); await hub.publish('incident',incident.model_dump())
    background(theft.forward_review(incident,payload.decision,payload.note)); return incident

@app.post('/api/jobs/{job_id}/retry',dependencies=[Depends(authenticated)])
def retry(job_id:str):
    job=get(Job,job_id)
    if not job: raise HTTPException(404)
    if job.status!='failed': raise HTTPException(409,'Only failed jobs may be retried')
    job.status='queued'; job.error=None; return save(job)

@app.post('/api/incidents/{incident_id}/compare',dependencies=[Depends(authenticated)])
def compare(incident_id:str):
    if not settings.enable_bvimamba:raise HTTPException(400,'The offline comparison model is not enabled')
    incident=get(Incident,incident_id)
    if not incident:raise HTTPException(404)
    if incident.record.get('expired'):raise HTTPException(410,'Source media has expired')
    existing=[j for j in rows(Job) if j.incident_id==incident_id and j.kind=='bvimamba' and j.status in ['queued','running']]
    return existing[0] if existing else save(Job(incident_id=incident_id,kind='bvimamba',priority=4))

@app.get('/api/incidents/{incident_id}/media/{kind}',dependencies=[Depends(authenticated)])
def media(incident_id:str,kind:str):
    incident=get(Incident,incident_id)
    if not incident: raise HTTPException(404)
    filename=incident.record.get('files',{}).get(kind)
    if not filename: raise HTTPException(404,'Artifact not available')
    path=settings.data_dir/'incidents'/incident_id/filename
    if not path.is_file(): raise HTTPException(404)
    return FileResponse(path,headers={'Cache-Control':'private, no-store'})

@app.get('/api/sources/{source_id}/frame',dependencies=[Depends(authenticated)])
def latest_frame(source_id:str,enhanced:bool=False):
    session=sessions.get(source_id)
    data=(session.enhanced_latest if enhanced else session.latest) if session else None
    if not data: raise HTTPException(404)
    return Response(data,media_type='image/jpeg',headers={'Cache-Control':'no-store'})

@app.websocket('/ws/events')
async def events(ws:WebSocket):
    if not hmac.compare_digest(ws.cookies.get('safesc_token',''),settings.app_token): await ws.close(4401); return
    await ws.accept(); q=asyncio.Queue(maxsize=32); hub.listeners.add(q)
    try:
        while True:
            try: event=await asyncio.wait_for(q.get(),20)
            except asyncio.TimeoutError: event={'type':'ping','payload':{}}
            await ws.send_json(event)
    except (WebSocketDisconnect,RuntimeError): pass
    finally: hub.listeners.discard(q)

@app.websocket('/ws/capture')
async def capture(ws:WebSocket):
    await ws.accept(); session=None
    try:
        hello=await asyncio.wait_for(ws.receive_json(),10)
        pairing=pairings.get(hello.get('token',''))
        if not pairing or pairing['expires']<time.time(): await ws.close(4401,'Pairing expired'); return
        if sessions or upload_in_progress: await ws.close(4409,'Another stream is active'); return
        source=get(Source,pairing['source_id']); session=VideoSession(source); sessions[source.id]=session
        await ws.send_json({'type':'ready','fps':settings.capture_fps,'source_id':source.id})
        index=0; first=None
        while True:
            meta=await asyncio.wait_for(ws.receive_json(),30)
            if meta.get('type')=='stop':
                await session.close(); session=None
                await ws.send_json({'type':'stopped'})
                break
            raw=await asyncio.wait_for(ws.receive_bytes(),10)
            if len(raw)>2*1024*1024: raise ValueError('Frame exceeds 2 MB')
            stamp=float(meta['timestamp'])
            if first is None: first=stamp
            stamp-=first
            if not math.isfinite(stamp) or stamp<0 or stamp>86400: raise ValueError('Invalid timestamp')
            await session.push(Frame(stamp,index,raw)); index+=1
            await ws.send_json({'type':'ack','index':index,'timestamp':stamp,'model_status':session.source.telemetry.get('model_status')})
    except WebSocketDisconnect: pass
    except Exception as e:
        try: await ws.send_json({'type':'error','message':str(e)[:500]})
        except RuntimeError: pass
    finally:
        if session: await session.close()
        try: await ws.close()
        except RuntimeError: pass

web=Path('apps/web/dist')
if web.exists():
    app.mount('/assets',StaticFiles(directory=web/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path:str): return FileResponse(web/'index.html')
