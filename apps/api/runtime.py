import asyncio
import json
import secrets
import time
from pathlib import Path
from collections import deque
from .config import settings
from .db import Source, Incident, Job, ReviewAction, save, get
from .schemas import Detection
from .video import CircularBuffer, LightingGate, Frame, decode, encode, digest, read_video, write_clip,select_evidence
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
        self.source=source; self.buffer=CircularBuffer(settings.pre_event_seconds+max(settings.post_event_seconds,settings.object_post_event_seconds)+settings.max_inference_lag_seconds+5)
        self.review_copies=source.telemetry.get('review_copies',True)
        self.gate=TemporalGate(settings.alert_confidence_threshold,settings.alert_cooldown_seconds,settings.alert_agreement_seconds)
        self.light=LightingGate(settings.dark_luminance_threshold,settings.light_luminance_threshold)
        self.detector=FixtureDetector() if settings.streaming_model=='fixture' else MossDetector(settings.moss_ws_url)
        self.pending=[]; self.latest=None; self.latest_frame=None; self.enhanced_latest=None; self.last_sample=-100
        self.tracker=None; self.tracks=[]; self.error=None; self.count=0; self.started=time.monotonic()
        self.enhancer=None; self.enhanced={}; self.observations=[]; self.last_observation=None
        self.inference_task=None; self.inference_queue=asyncio.Queue(maxsize=1)
        self.closing=False; self.inference_frames=0; self.skipped_inference_frames=0
        self.inference_state='not_started'
        self.last_saved=0
        self.last_bookmark=-1e9
        self.persistence_tasks=set();self.review_alert=None;self.chat_notes=[];self.last_chat_id=0
        self.last_published=0;self.persistence_errors=[];self.review_alerts=[];self.previous_thumbnail=None
        self.model_update_task=None
        self.detector.on_update=self.model_updated
    def model_updated(self):
        """Coalesce native token bursts; never wait for the next camera frame."""
        if self.closing:return
        if self.model_update_task is None or self.model_update_task.done():
            self.model_update_task=asyncio.create_task(self.publish_model_update())
    def collect_notes(self,current_time):
        for note in getattr(self.detector,'observer_notes',[]):
            if note['id']>self.last_chat_id:
                self.chat_notes.append({**note,'received_stream_time':current_time})
                self.chat_notes=self.chat_notes[-30:];self.last_chat_id=note['id']
    def analysis_telemetry(self):
        self.error=getattr(self.detector,'error',self.error)
        return {'error':self.error,
            'model_status':'error' if self.error else ('waiting_for_gpu' if self.inference_state=='waiting_for_gpu' else getattr(self.detector,'phase',self.inference_state)),
            'moss_chat':list(self.chat_notes),'review_alert':self.review_alert,'review_alerts':list(self.review_alerts),
            'last_observation':self.last_observation,
            'safety_state':self.last_observation['state'] if self.last_observation else 'AWAITING_ASSESSMENT',
            'alert_gate':{'threshold':settings.alert_confidence_threshold,'reason':self.gate.last_reason},
            'verification_windows':list(getattr(self.detector,'verification_summaries',[])),
            'verification_progress':getattr(self.detector,'verification_progress',None),
            'streaming_notes':getattr(self.detector,'observer_text','')[-600:],
            'moss_objects':{k:v for k,v in (getattr(self.detector,'object_sample',None) or {}).items() if k!='jpeg'}}
    async def publish_model_update(self):
        # A small bounded coalescing window avoids one WebSocket message per token.
        await asyncio.sleep(.025)
        if self.closing:return
        current_time=self.latest_frame.timestamp if self.latest_frame else 0
        self.collect_detections(current_time)
        self.collect_notes(current_time)
        self.source.telemetry={**self.source.telemetry,**self.analysis_telemetry()}
        await hub.publish('source',self.source.model_dump())
        self.last_published=time.monotonic()
    def set_alert(self,alert):
        self.review_alert=alert
        for n,item in enumerate(self.review_alerts):
            if item['id']==alert['id']:self.review_alerts[n]=alert;break
        else:self.review_alerts.append(alert);self.review_alerts=self.review_alerts[-10:]
    def update_alert(self,notice_id,**changes):
        for n,alert in enumerate(self.review_alerts):
            if alert['id']==notice_id:
                changed={**alert,**changes};self.review_alerts[n]=changed
                if self.review_alert and self.review_alert['id']==notice_id:self.review_alert=changed
                self.model_updated()
                break
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
    def refresh_evidence(self,current_time):
        # Capture keeps filling the original buffer during GPU/model startup.
        # Refresh again at shutdown so a short input cannot end with only its
        # first frame in the verifier's retained context.
        previous=getattr(self.detector,'last_verified',current_time-4)
        coverage=min(settings.max_inference_lag_seconds,max(4,current_time-max(0,previous)+1))
        pool=self.buffer.window(max(0,current_time-coverage),current_time)
        self.detector.evidence_pool=list(pool)
        self.detector.evidence_frames=select_evidence(pool,settings.verification_frame_count)
    async def observe(self,frame,image,lighting):
        started=time.monotonic()
        try:
            if settings.enable_tracking:
                if not self.tracker:self.tracker=await asyncio.to_thread(ByteTracker,settings.sample_fps)
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
            self.refresh_evidence(frame.timestamp)
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
            if current_time-event_end>settings.max_inference_lag_seconds:
                self.gate.last_reason='evidence_too_old';continue
            eligible=self.buffer.window(max(0,event_end-settings.pre_event_seconds),event_end)
            from hashlib import sha256
            visual_evidence={sha256(f.jpeg).digest() for f in eligible if f.index in detection.supporting_frames}
            if not detection.simulated and detection.state in ['REVIEW_REQUIRED','HIGH_PRIORITY_REVIEW'] and len(visual_evidence)<2:
                self.gate.last_reason='duplicate_visual_evidence';continue
            if self.gate.consider(detection,current_time,[f.index for f in eligible]):
                # Center evidence on the observed event, not late model completion.
                notice_id=secrets.token_hex(8)
                post_seconds=max(settings.post_event_seconds,settings.object_post_event_seconds) if detection.action=='object_taking' else settings.post_event_seconds
                self.set_alert({'id':notice_id,'status':'saving_context','timestamp':event_end,
                    'received_stream_time':current_time,'action':detection.action,
                    'confidence':detection.confidence,'threshold':settings.alert_confidence_threshold,
                    'message':detection.observable_events[0].observation,'post_seconds':post_seconds,
                    'label':'Possible incident — review required','origin':'temporal_review_gate',
                    'evidence_rule':'Two corroborating original-frame sets; human review required'})
                self.pending.append({'trigger':event_end,'detection':detection,'notice_id':notice_id,'post_seconds':post_seconds,
                    'output_audit':list(getattr(self.detector,'response_audit',[]))})
    async def push(self,frame):
        self.buffer.append(frame); self.count+=1
        image=await asyncio.to_thread(decode,frame.jpeg)
        import cv2
        import numpy as np
        thumbnail=cv2.resize(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),(96,54))
        if self.previous_thumbnail is not None:
            difference=cv2.absdiff(thumbnail,self.previous_thumbnail).ravel()
            frame.motion=float(np.mean(np.partition(difference,-max(1,len(difference)//10))[-max(1,len(difference)//10):]))
        self.previous_thumbnail=thumbnail
        self.latest=frame.jpeg;self.latest_frame=frame
        lighting=await asyncio.to_thread(self.light.update,image)
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
            if frame.timestamp>=candidate['trigger']+candidate.get('post_seconds',settings.post_event_seconds):
                # Snapshot references now; CPU clip encoding must not stall phone ACKs.
                frozen=self.buffer.window(max(0,candidate['trigger']-settings.pre_event_seconds),min(frame.timestamp,candidate['trigger']+candidate.get('post_seconds',settings.post_event_seconds)))
                task=asyncio.create_task(self.persist_safely(candidate,frame.timestamp,frozen))
                self.persistence_tasks.add(task);task.add_done_callback(self.persistence_tasks.discard)
                self.pending.remove(candidate)
        keep={f.index for f in self.buffer.frames}
        self.enhanced={i:f for i,f in self.enhanced.items() if i in keep}
        previous_chat_id=self.last_chat_id
        self.collect_notes(frame.timestamp)
        self.source.status='streaming'
        self.source.telemetry={'review_copies':self.review_copies,'timestamp':frame.timestamp,'frame_count':self.count,'lighting':lighting,
            'capture_active':True,'stream_ended':False,
            'frame_width':int(image.shape[1]),'frame_height':int(image.shape[0]),
            'buffer_seconds':frame.timestamp-self.buffer.frames[0].timestamp,'buffer_bytes':self.buffer.size,
            'model':settings.streaming_model,'model_status':'error' if self.error else ('waiting_for_gpu' if self.inference_state=='waiting_for_gpu' else getattr(self.detector,'phase',self.inference_state)),
            'error':self.error,'tracks':self.tracks,'pending_incidents':len(self.pending),
            'moss_chat':self.chat_notes,'review_alert':self.review_alert,'review_alerts':self.review_alerts,
            'alert_gate':{'threshold':settings.alert_confidence_threshold,'reason':self.gate.last_reason},
            'verification_windows':list(getattr(self.detector,'verification_summaries',[])),
            'verification_progress':getattr(self.detector,'verification_progress',None),
            'recording_errors':self.persistence_errors,
            'moss_objects':{k:v for k,v in (getattr(self.detector,'object_sample',None) or {}).items() if k!='jpeg'},
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
        if time.monotonic()-self.last_published>=.4 or self.count==1 or self.last_chat_id!=previous_chat_id:
            await hub.publish('source',self.source.model_dump());self.last_published=time.monotonic()
    async def persist_safely(self,candidate,end,frames):
        try:await self.persist(candidate,end,frames)
        except Exception as error:
            self.persistence_errors.append(str(error))
            self.update_alert(candidate.get('notice_id'),status='failed',error=str(error))
            await hub.publish('recording_error',{'source_id':self.source.id,'error':str(error)})
    async def persist(self,candidate,end,frozen_frames=None):
        trigger=candidate['trigger']; d=candidate['detection']
        frames=frozen_frames if frozen_frames is not None else self.buffer.window(max(0,trigger-settings.pre_event_seconds),min(end,trigger+candidate.get('post_seconds',settings.post_event_seconds)))
        if not frames: return
        incident=Incident(source_id=self.source.id,start_time=frames[0].timestamp,end_time=frames[-1].timestamp,
            severity=d.state,confidence=d.confidence,simulated=d.simulated)
        folder=settings.data_dir/'incidents'/incident.id; folder.mkdir(parents=True)
        original=folder/'original.mp4'
        enhanced=[self.enhanced[f.index] for f in frames if f.index in self.enhanced]
        lighting_at_save=self.light.state
        from .recording import export_clip
        native = await export_clip(self.source.id, getattr(self, 'capture_epoch', None),
            frames[0].timestamp, frames[-1].timestamp, original,
            wait_for_registration=getattr(self, 'native_recording_expected', False)) if self.source.kind in ('phone', 'webcam') else None
        if native:
            incident.start_time = native['source_start']
            incident.end_time = native['source_end']
        else:
            await asyncio.to_thread(write_clip,frames,original)
        thumbnail=folder/'thumbnail.jpg'; thumbnail.write_bytes(frames[len(frames)//2].jpeg)
        source_hash=self.source.original_sha256
        record={'action':d.action,'observable_events':[x.model_dump() for x in d.observable_events], 'uncertainties':d.uncertainties,
            'supporting_frames':d.supporting_frames,'model':{'name':settings.streaming_model,'revision':settings.model_revision},
            'visual_verifier':settings.observation_verifier_model,
            'observation_model':{'name':'Qwen/Qwen3-VL-4B-Instruct','revision':getattr(getattr(self.detector,'verifier',None),'revision','unconfigured'),
                'adapter_sha256':getattr(getattr(self.detector,'verifier',None),'adapter','') or None,'research_mode':settings.verifier_research_mode} if settings.observation_verifier_model=='qwen3vl' else {'name':settings.streaming_model,'revision':settings.model_revision},
            'lighting_condition':lighting_at_save,'enhancement_used':bool(enhanced),
            'original_sha256':await asyncio.to_thread(digest,original),'source_sha256':source_hash,
            'source_file':self.source.path,'files':{'original':'original.mp4','thumbnail':'thumbnail.jpg'},
            'simulated':d.simulated,'trigger_time':trigger,'clip_offset':incident.start_time,
            'recording':{k:v for k,v in native.items() if k!='native_frame_timestamps'} if native else {'kind':'jpeg_context_fallback','reason':'Complete native encoded window unavailable; saved frame rate is the received evidence frame rate'},
            'native_frame_timestamps':native['native_frame_timestamps'] if native else None,
            'trigger_origin':candidate.get('origin','model'),
            'transforms':[{'operation':native['container_operation'] if native else 'incident extraction and H.264 encoding','source':'native browser camera stream' if native else 'received camera JPEGs or decoded uploaded video',
                'timestamps':'original stream seconds; clip starts at zero','original_upload_untouched':bool(source_hash)}],
            'prompt_version':'safesc-observable-v2','analysis_only_transforms':['Source index/time margin and anonymous track boxes; not applied to stored originals'],
            'moss_observations':[n for n in self.chat_notes if frames[0].timestamp-10<=n['received_stream_time']<=end],
            'alert_gate':{'threshold':settings.alert_confidence_threshold,'rule':'two original-frame sets','score':d.confidence},
            'sampling_policy':'Original context and motion transitions; motion only selects frames, never triggers incidents',
            'review_copies_requested':self.review_copies,
            'capture_window':{'pre_seconds':settings.pre_event_seconds,'post_seconds':candidate.get('post_seconds',settings.post_event_seconds)},
            'model_output_audit':candidate.get('output_audit',list(getattr(self.detector,'response_audit',[]))),
            'confidence_note':'Uncalibrated model score, not probability of harm',
            'inference_error':self.error,'frame_timestamps':[{'index':f.index,'timestamp':f.timestamp} for f in frames]}
        if enhanced:
            await asyncio.to_thread(write_clip,enhanced,folder/'enhanced.mp4','AI-ENHANCED REVIEW COPY')
            record['files']['enhanced']='enhanced.mp4'
            record['artifact_time_offsets']={'enhanced':enhanced[0].timestamp-incident.start_time}
            record['transforms'].append({'operation':'FastLLVE','sample_fps':settings.sample_fps,'checkpoint':Path(settings.fastllve_checkpoint).name})
        incident.record=record; await asyncio.to_thread(save,incident)
        if candidate.get('origin')=='reviewer':
            incident.record={**incident.record,'confidence_note':'Human bookmark; no AI detection score is asserted'}
            await asyncio.to_thread(save,incident)
            await asyncio.to_thread(save,ReviewAction(incident_id=incident.id,decision='needs_review',note='Reviewer marked this moment from the source feed'))
        self.update_alert(candidate.get('notice_id'),status='saved',incident_id=incident.id)
        for kind,enabled in [('review_brief',settings.enable_clip_review),('relayvsr',settings.enable_relay_vsr),('relift',settings.enable_relift)]:
            if enabled and (kind=='review_brief' or self.review_copies): await asyncio.to_thread(save,Job(incident_id=incident.id,kind=kind,priority=0 if kind=='review_brief' else 2 if kind=='relayvsr' else 3))
        await hub.publish('incident',incident.model_dump())
    async def close(self):
        self.closing=True
        self.source.status='processing'
        self.source.telemetry={**self.source.telemetry,'capture_active':False,'stream_ended':True}
        await asyncio.to_thread(save,self.source)
        await hub.publish('source',self.source.model_dump())
        if self.model_update_task:await self.model_update_task
        if self.inference_task:
            if self.inference_queue.full():self.inference_queue.get_nowait()
            self.inference_queue.put_nowait(None)
            # Finish the current GPU call before releasing resources or starting another model.
            await self.inference_task
        if self.buffer.frames:self.refresh_evidence(self.buffer.frames[-1].timestamp)
        if hasattr(self.detector,'finish'):await self.detector.finish()
        self.error=getattr(self.detector,'error',self.error)
        if self.buffer.frames:
            self.collect_detections(self.buffer.frames[-1].timestamp)
            self.collect_notes(self.buffer.frames[-1].timestamp)
        for candidate in self.pending:
            if self.buffer.frames: await self.persist(candidate,self.buffer.frames[-1].timestamp)
        self.pending.clear()
        if self.persistence_tasks:await asyncio.gather(*list(self.persistence_tasks))
        await self.detector.close()
        # Compatible realtime models stay warm; the idle job worker unloads them
        # before a heavy subprocess, and API shutdown releases them explicitly.
        self.enhancer=None; self.tracker=None
        if 'torch' in __import__('sys').modules:
            import torch
            if torch.cuda.is_available(): torch.cuda.empty_cache()
        from .recording import cleanup
        await cleanup(self.source.id)
        self.source.status='failed' if self.error else 'completed'; await asyncio.to_thread(save,self.source)
        self.source.telemetry={**self.source.telemetry,'error':self.error,
            'model_status':'error' if self.error else 'completed','last_observation':self.last_observation,
            'moss_chat':self.chat_notes,'review_alert':self.review_alert,'review_alerts':self.review_alerts,
            'alert_gate':{'threshold':settings.alert_confidence_threshold,'reason':self.gate.last_reason},
            'verification_windows':list(getattr(self.detector,'verification_summaries',[])),
            'verification_progress':getattr(self.detector,'verification_progress',None),
            'recording_errors':self.persistence_errors,
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
