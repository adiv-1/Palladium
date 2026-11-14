import asyncio
import json
import secrets
import time
from pathlib import Path
from collections import deque
from .config import settings
from .db import Source, Incident, Job, ReviewAction, save, get
from .schemas import Detection
from .video import CircularBuffer, LightingGate, Frame, decode, encode, digest, read_video, write_clip
from ml.adapters import MossDetector, FixtureDetector, TemporalGate, ByteTracker, UnavailableModel

class Hub:
    def __init__(self): self.listeners=set()
    async def publish(self,kind,payload):
        for q in list(self.listeners):
            if q.full():
                try: q.get_nowait()
                except asyncio.QueueEmpty: pass
            q.put_nowait({'type':kind,'payload':payload})
hub=Hub()
sessions={}
pairings={}

class VideoSession:
    def __init__(self,source):
        self.source=source; self.buffer=CircularBuffer(settings.pre_event_seconds+settings.post_event_seconds+5)
        self.gate=TemporalGate(settings.alert_confidence_threshold,settings.alert_cooldown_seconds,settings.alert_agreement_seconds)
        self.light=LightingGate(settings.dark_luminance_threshold,settings.light_luminance_threshold)
        self.detector=FixtureDetector() if settings.streaming_model=='fixture' else MossDetector(settings.moss_ws_url)
        self.pending=[]; self.latest=None; self.enhanced_latest=None; self.last_sample=-100
        self.tracker=None; self.tracks=[]; self.error=None; self.count=0; self.started=time.monotonic()
        self.enhancer=None; self.enhanced={}; self.observations=[]; self.last_observation=None
        self.inference_task=None; self.inference_queue=asyncio.Queue(maxsize=1)
        self.closing=False; self.inference_frames=0; self.skipped_inference_frames=0
        self.inference_state='not_started'
        self.last_saved=0
        self.last_bookmark=-1e9
    def bookmark(self):
        if len(self.buffer.frames)<2:raise ValueError('Wait for a few video frames before marking a moment')
        end=self.buffer.frames[-1].timestamp
        if end-self.last_bookmark<2:raise ValueError('This moment is already marked')
        self.last_bookmark=end
        detection=Detection(state='WATCH',confidence=0,simulated=settings.streaming_model=='fixture',
            observable_events=[{'timestamp_start':max(0,end-1),'timestamp_end':end,'actors':[],
                'observation':'Reviewer marked this moment for review'}],
            uncertainties=['Reviewer-requested bookmark; this is not an AI incident claim.'],
            supporting_frames=[f.index for f in list(self.buffer.frames)[-2:]])
        self.pending.append({'trigger':end,'detection':detection,'origin':'reviewer',
            'output_audit':list(getattr(self.detector,'response_audit',[]))})
        return {'status':'marked','timestamp':end,'context':'Surrounding footage is saved after the post-event window or when the stream stops'}
    async def prepare_upload(self):
        from . import jobs
        self.source.status='waiting_for_gpu';save(self.source)
        while jobs.gpu_busy:await asyncio.sleep(.2)
        self.source.status='warming';save(self.source)
        if settings.enable_tracking:
            self.tracker=await asyncio.to_thread(ByteTracker,settings.sample_fps)
            # Warm the detector before advancing an uploaded clip's playback clock.
            import numpy as np
            await asyncio.to_thread(self.tracker.update,np.zeros((360,640,3),dtype=np.uint8),0)
        if settings.enhancement_model=='fastllve':
            from ml.fastllve import FastLLVE
            self.enhancer=await asyncio.to_thread(FastLLVE,settings.fastllve_checkpoint)
        from ml.service import model_service
        await model_service.ensure()
        if hasattr(self.detector,'prepare_verifier'):await self.detector.prepare_verifier()
    async def observe(self,frame,image,lighting):
        started=time.monotonic()
        try:
            if settings.enable_tracking:
                if not self.tracker: self.tracker=await asyncio.to_thread(ByteTracker,settings.sample_fps)
                self.tracks=await asyncio.to_thread(self.tracker.update,image,frame.timestamp)
            sampled=frame
            if lighting['state']!='well_lit' and settings.enhancement_model=='fastllve':
                from ml.fastllve import FastLLVE
                if not self.enhancer: self.enhancer=await asyncio.to_thread(FastLLVE,settings.fastllve_checkpoint)
                recent=self.buffer.window(max(0,frame.timestamp-3),frame.timestamp)
                output=await asyncio.to_thread(self.enhancer.enhance,[decode(f.jpeg) for f in recent] or [image],lighting)
                sampled=Frame(frame.timestamp,frame.index,encode(output[-1])); self.enhanced_latest=sampled.jpeg
                self.enhanced[frame.index]=sampled
            if settings.streaming_model=='onestreamer': raise UnavailableModel('OneStreamer checkpoint not configured')
            evidence=self.buffer.window(max(0,frame.timestamp-4),frame.timestamp)
            if len(evidence)>4:evidence=[evidence[round(i*(len(evidence)-1)/3)] for i in range(4)]
            self.detector.evidence_frames=evidence
            self.inference_state='loading' if not getattr(self.detector,'ws',True) else 'observing'
            await self.detector.observe([sampled],[frame.timestamp],self.tracks)
            self.error=getattr(self.detector,'error',None)
            self.inference_frames+=1; self.inference_state='observing'
        except Exception as e:
            self.error=f'{type(e).__name__}: {e}'; self.inference_state='error'
        self.last_processing_ms=(time.monotonic()-started)*1000
    async def inference_loop(self):
        from . import jobs
        while not self.closing:
            item=await self.inference_queue.get()
            if item is None: return
            while jobs.gpu_busy and not self.closing:
                self.inference_state='waiting_for_gpu'
                await asyncio.sleep(.2)
            if self.closing: return
            # Keep the most recent sampled frame when the GPU has been occupied.
            while not self.inference_queue.empty():
                newer=self.inference_queue.get_nowait()
                if newer is None:return
                item=newer; self.skipped_inference_frames+=1
            await self.observe(*item)
    def collect_detections(self,current_time):
        for detection in self.detector.drain():
            self.last_observation=detection.model_dump()
            self.observations.append(self.last_observation);self.observations=self.observations[-100:]
            event_end=max((event.timestamp_end for event in detection.observable_events),default=current_time)
            eligible=self.buffer.window(max(0,event_end-settings.pre_event_seconds),event_end)
            if self.gate.consider(detection,current_time,[f.index for f in eligible]):
                # Center evidence on the observed event, not late model completion.
                self.pending.append({'trigger':event_end,'detection':detection,
                    'output_audit':list(getattr(self.detector,'response_audit',[]))})
    async def push(self,frame):
        self.buffer.append(frame); self.latest=frame.jpeg; self.count+=1
        image=await asyncio.to_thread(decode,frame.jpeg)
        lighting=self.light.update(image)
        if frame.timestamp-self.last_sample>=1/settings.sample_fps:
            self.last_sample=frame.timestamp
            if settings.streaming_model=='fixture':
                await self.observe(frame,image,lighting)
            else:
                if self.inference_task is None:self.inference_task=asyncio.create_task(self.inference_loop())
                if self.inference_queue.full():
                    self.inference_queue.get_nowait();self.skipped_inference_frames+=1
                self.inference_queue.put_nowait((frame,image,lighting))
        self.collect_detections(frame.timestamp)
        for candidate in list(self.pending):
            if frame.timestamp>=candidate['trigger']+settings.post_event_seconds:
                await self.persist(candidate,frame.timestamp)
                self.pending.remove(candidate)
        keep={f.index for f in self.buffer.frames}
        self.enhanced={i:f for i,f in self.enhanced.items() if i in keep}
        self.source.status='streaming'
        self.source.telemetry={'timestamp':frame.timestamp,'frame_count':self.count,'lighting':lighting,
            'frame_width':int(image.shape[1]),'frame_height':int(image.shape[0]),
            'buffer_seconds':frame.timestamp-self.buffer.frames[0].timestamp,'buffer_bytes':self.buffer.size,
            'model':settings.streaming_model,'model_status':'error' if self.error else ('waiting_for_gpu' if self.inference_state=='waiting_for_gpu' else getattr(self.detector,'phase',self.inference_state)),
            'error':self.error,'tracks':self.tracks,'pending_incidents':len(self.pending),
            'safety_state':self.last_observation['state'] if self.last_observation else 'AWAITING_ASSESSMENT',
            'last_observation':self.last_observation,'simulated':settings.streaming_model=='fixture',
            'visual_verifier':settings.observation_verifier_model,
            'streaming_notes':getattr(self.detector,'observer_text','')[-600:],
            'frame_processing_ms':getattr(self,'last_processing_ms',0),
            'model_transport_ms':getattr(self.detector,'latency_ms',0),
            'inference_frames':self.inference_frames,'skipped_inference_frames':self.skipped_inference_frames,
            'processing_fps':self.count/max(.01,time.monotonic()-self.started)}
        if time.monotonic()-self.last_saved>=1 or self.count==1:
            await asyncio.to_thread(save,self.source);self.last_saved=time.monotonic()
        await hub.publish('source',self.source.model_dump())
    async def persist(self,candidate,end):
        trigger=candidate['trigger']; d=candidate['detection']
        frames=self.buffer.window(max(0,trigger-settings.pre_event_seconds),min(end,trigger+settings.post_event_seconds))
        if not frames: return
        incident=Incident(source_id=self.source.id,start_time=frames[0].timestamp,end_time=frames[-1].timestamp,
            severity=d.state,confidence=d.confidence,simulated=d.simulated)
        folder=settings.data_dir/'incidents'/incident.id; folder.mkdir(parents=True)
        original=folder/'original.mp4'
        await asyncio.to_thread(write_clip,frames,original)
        thumbnail=folder/'thumbnail.jpg'; thumbnail.write_bytes(frames[len(frames)//2].jpeg)
        source_hash=self.source.original_sha256
        record={'observable_events':[x.model_dump() for x in d.observable_events], 'uncertainties':d.uncertainties,
            'supporting_frames':d.supporting_frames,'model':{'name':settings.streaming_model,'revision':settings.model_revision},
            'visual_verifier':settings.observation_verifier_model,
            'observation_model':{'name':'Qwen/Qwen3-VL-4B-Instruct','revision':getattr(getattr(self.detector,'verifier',None),'revision','unconfigured')} if settings.observation_verifier_model=='qwen3vl' else {'name':settings.streaming_model,'revision':settings.model_revision},
            'lighting_condition':self.light.state,'enhancement_used':bool(self.enhanced),
            'original_sha256':await asyncio.to_thread(digest,original),'source_sha256':source_hash,
            'source_file':self.source.path,'files':{'original':'original.mp4','thumbnail':'thumbnail.jpg'},
            'simulated':d.simulated,'trigger_time':trigger,'clip_offset':frames[0].timestamp,
            'trigger_origin':candidate.get('origin','model'),
            'transforms':[{'operation':'incident extraction and H.264 encoding','source':'received camera JPEGs or decoded uploaded video',
                'timestamps':'original stream seconds; clip starts at zero','original_upload_untouched':bool(source_hash)}],
            'prompt_version':'safesc-observable-v2','analysis_only_transforms':['Source index/time margin and anonymous track boxes; not applied to stored originals'],
            'model_output_audit':candidate.get('output_audit',list(getattr(self.detector,'response_audit',[]))),
            'confidence_note':'Uncalibrated model score, not probability of harm',
            'inference_error':self.error,'frame_timestamps':[{'index':f.index,'timestamp':f.timestamp} for f in frames]}
        enhanced=[self.enhanced[f.index] for f in frames if f.index in self.enhanced]
        if enhanced:
            await asyncio.to_thread(write_clip,enhanced,folder/'enhanced.mp4','AI-ENHANCED REVIEW COPY')
            record['files']['enhanced']='enhanced.mp4'
            record['transforms'].append({'operation':'FastLLVE','sample_fps':settings.sample_fps,'checkpoint':Path(settings.fastllve_checkpoint).name})
        incident.record=record; await asyncio.to_thread(save,incident)
        if candidate.get('origin')=='reviewer':
            incident.record={**incident.record,'confidence_note':'Human bookmark; no AI detection score is asserted'}
            await asyncio.to_thread(save,incident)
            await asyncio.to_thread(save,ReviewAction(incident_id=incident.id,decision='needs_review',note='Reviewer marked this moment from the source feed'))
        for kind,enabled in [('relayvsr',settings.enable_relay_vsr),('relift',settings.enable_relift)]:
            if enabled: await asyncio.to_thread(save,Job(incident_id=incident.id,kind=kind,priority=2 if kind=='relayvsr' else 3))
        await hub.publish('incident',incident.model_dump())
    async def close(self):
        self.closing=True
        if self.inference_task:
            if self.inference_queue.full():self.inference_queue.get_nowait()
            self.inference_queue.put_nowait(None)
            # Finish the current GPU call before releasing resources or starting another model.
            await self.inference_task
        if hasattr(self.detector,'finish'):await self.detector.finish()
        self.error=getattr(self.detector,'error',self.error)
        if self.buffer.frames:self.collect_detections(self.buffer.frames[-1].timestamp)
        for candidate in self.pending:
            if self.buffer.frames: await self.persist(candidate,self.buffer.frames[-1].timestamp)
        self.pending.clear(); await self.detector.close()
        # Compatible realtime models stay warm; the idle job worker unloads them
        # before a heavy subprocess, and API shutdown releases them explicitly.
        self.enhancer=None; self.tracker=None
        if 'torch' in __import__('sys').modules:
            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
        self.source.status='failed' if self.error else 'completed'; await asyncio.to_thread(save,self.source)
        self.source.telemetry={**self.source.telemetry,'error':self.error,
            'model_status':'error' if self.error else 'completed','last_observation':self.last_observation,
            'safety_state':self.last_observation['state'] if self.last_observation else 'AWAITING_ASSESSMENT',
            'inference_frames':self.inference_frames}
        await asyncio.to_thread(save,self.source)
        sessions.pop(self.source.id,None)
        await hub.publish('source',self.source.model_dump())

async def replay(source,path):
    session=VideoSession(source); sessions[source.id]=session
    iterator=read_video(path)
    def next_frame(): return next(iterator,None)
    try:
        if settings.streaming_model!='fixture':await session.prepare_upload()
        started=time.monotonic()
        while (frame:=await asyncio.to_thread(next_frame)) is not None:
            await asyncio.sleep(max(0,frame.timestamp-(time.monotonic()-started)))
            await session.push(frame)
    except Exception as e:
        session.error=str(e); source.telemetry={'error':str(e)}; source.status='failed'; save(source)
    finally:
        await session.close()
