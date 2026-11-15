"""Real phone-protocol replay. Expected labels never enter model prompts or alert logic.

One stream contains full held-out source videos separated by neutral context.
No bookmark or simulated detector is used. This is a diagnostic on previously
evaluated academic videos, not an independent accuracy or physical-phone study.
"""
import argparse,asyncio,io,json,time
from pathlib import Path
import av,httpx,websockets
from PIL import Image

def wire_jpeg(image):
    image=image.convert('RGB');image.thumbnail((960,540));canvas=Image.new('RGB',(960,540));canvas.paste(image,((960-image.width)//2,(540-image.height)//2));out=io.BytesIO();canvas.save(out,format='JPEG',quality=72);return out.getvalue()

def materialize(path,fps=6):
    frames=[];next_time=0
    with av.open(str(path)) as c:
        stream=c.streams.video[0];duration=float(stream.duration*stream.time_base)
        for frame in c.decode(video=0):
            t=float(frame.time or 0)
            if t+1e-6<next_time:continue
            frames.append((t,wire_jpeg(frame.to_image())));next_time=t+1/fps
    return duration,frames

async def main(origin,output,only=None):
    token=Path('data/runpod_app_token').read_text().strip();root=Path('data/learning-research/videos')
    neutral=wire_jpeg(Image.open('data/smoke/bus.jpg'))
    segments=[];at=0
    def add(name,duration,frames,expected=None):
        nonlocal at
        segments.append({'name':name,'start':at,'end':at+duration,'frames':frames,'expected':expected});at+=duration
    for name,action in [('Assault038_x264','physical_contact'),('Stealing081_x264','object_taking'),
                        ('Normal_Videos609_x264','ordinary_activity'),('Normal_Videos610_x264','ordinary_activity')]:
        if only and action!=only:continue
        duration,frames=materialize(root/(name+'.mp4'))
        add('same-camera static lead-up',12,[(0,frames[0][1])])
        add(name,duration,frames,action)
        add('same-camera static follow-through',24,[(0,frames[-1][1])])
    records=[json.loads(x) for x in Path('data/learning/pilot/records.jsonl').read_text().splitlines()]
    expected=[]
    for segment in segments:
        if not segment['expected']:continue
        record=next(r for r in records if r['video_id']==segment['name'])
        expected.append({k:v for k,v in segment.items() if k!='frames'}|{'annotated_start':segment['start']+record['timestamps'][0],
            'annotated_end':segment['start']+record['timestamps'][-1]})
    headers={'Authorization':'Bearer '+token}
    async with httpx.AsyncClient(base_url=origin,headers=headers,timeout=240) as c:
        h=await c.get('/api/health');h.raise_for_status();assert h.json()['active_sources']==0
        warm=await c.post('/api/models/moss/warm');warm.raise_for_status()
        r=await c.post('/api/sources',json={'name':'Research acceptance · real video replay · no human bookmark','kind':'phone','review_copies':False})
        r.raise_for_status();pair=r.json();source_id=pair['source']['id'];print('Source',source_id,'duration',round(at,1),flush=True)
        ack_times=[];pending={};alerts={};started=time.monotonic();index=0;phase=-1;first_wire_stamp=None
        async with websockets.connect(origin.replace('https:','wss:')+'/ws/capture',max_size=2**22) as ws:
            await ws.send(json.dumps({'token':pair['capture_url'].split('#')[1]}));assert json.loads(await ws.recv())['type']=='ready'
            async def receive():
                async for raw in ws:
                    event=json.loads(raw)
                    if event['type']=='ack':ack_times.append((time.monotonic()-pending.pop(event['index']-1))*1000)
                    elif event['type']=='phone_alert':continue  # Source-scoped camera notice; ACK accounting is independent.
                    elif event['type']=='stopped':return
                    else:raise RuntimeError(str(event))
            async def notices():
                while True:
                    r=await c.get('/api/sources');r.raise_for_status();s=next(s for s in r.json() if s['id']==source_id)
                    for notice in s['telemetry'].get('review_alerts',[]):
                        if alerts.get(notice['id'],{}).get('status')!=notice['status']:
                            print('ALERT',json.dumps(notice),flush=True)
                        alerts[notice['id']]=notice
                    await asyncio.sleep(1)
            receiver=asyncio.create_task(receive());watcher=asyncio.create_task(notices())
            try:
                while (elapsed:=time.monotonic()-started)<at:
                    if receiver.done():await receiver;raise RuntimeError('Camera stopped early')
                    slot=next(i for i,s in enumerate(segments) if s['start']<=elapsed<s['end'])
                    segment=segments[slot]
                    if slot!=phase:phase=slot;print('Scene',segment['name'],'at',round(elapsed,2),flush=True)
                    if len(pending)<2:
                        source_time=elapsed-segment['start'];frames=segment['frames']
                        frame=frames[min(len(frames)-1,max(0,round(source_time*6)))]
                        pending[index]=time.monotonic()
                        if first_wire_stamp is None:first_wire_stamp=elapsed
                        await ws.send(json.dumps({'type':'frame','timestamp':elapsed}));await ws.send(frame[1]);index+=1
                    await asyncio.sleep(1/12)
                while pending:
                    if receiver.done():await receiver;raise RuntimeError('Missing capture acknowledgements')
                    await asyncio.sleep(.02)
                await ws.send(json.dumps({'type':'stop'}));await asyncio.wait_for(receiver,240)
            finally:
                receiver.cancel();watcher.cancel();await asyncio.gather(receiver,watcher,return_exceptions=True)
        source=next(s for s in (await c.get('/api/sources')).json() if s['id']==source_id)
        incidents=[i for i in (await c.get('/api/incidents')).json() if i['source_id']==source_id]
        for i in incidents:
            assert i['record']['trigger_origin']=='model' and not i['simulated']
            media=await c.get(f"/api/incidents/{i['id']}/media/original");media.raise_for_status()
            import hashlib
            assert hashlib.sha256(media.content).hexdigest()==i['record']['original_sha256']
    results=[]
    for window in expected:
        window={k:(v-first_wire_stamp if k in ['start','end','annotated_start','annotated_end'] else v) for k,v in window.items()}
        matching=[i for i in incidents if any(e['timestamp_end']>=window['start'] and e['timestamp_start']<=window['end'] for e in i['record']['observable_events'])]
        results.append({**window,'incident_ids':[i['id'] for i in matching],
            'observed_actions':[i['record'].get('action') for i in matching],
            'annotated_activity_in_saved_original':any(i['start_time']<=window['annotated_start'] and i['end_time']>=window['annotated_end'] for i in matching)})
    report={'source_id':source_id,'input':'Full previously evaluated research videos through real phone wire protocol; labels retained only in this report',
        'physical_phone_tested':False,'independent_accuracy_benchmark':False,'simulated_inference':False,
        'runtime_seconds':time.monotonic()-started,'first_wire_timestamp':first_wire_stamp,'frames_sent':index,'max_capture_ack_ms':max(ack_times),
        'windows':results,'incidents':incidents,'source':source}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['incidents','source']},indent=2),flush=True)
    assert all(w['incident_ids'] for w in results if w['expected']!='ordinary_activity'),'A positive scene produced no automatic incident'
    assert all(w['expected'] in w['observed_actions'] for w in results if w['expected']!='ordinary_activity'),'A positive scene was flagged with the wrong action category'
    assert all(w['annotated_activity_in_saved_original'] for w in results if w['expected']!='ordinary_activity'),'A saved original did not cover the annotated activity'
    assert all(i['record']['original_sha256'] for i in incidents)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--origin',required=True);p.add_argument('--output',type=Path,default=Path('data/event-stream-smoke-aligned.json'));p.add_argument('--only',choices=['physical_contact','object_taking','ordinary_activity']);a=p.parse_args()
    asyncio.run(main(a.origin,a.output,a.only))
