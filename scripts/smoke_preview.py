"""Measure authenticated camera-to-preview transport with a static public image.

This exercises real MOSS and the phone protocol, but is neither a physical-phone
test nor a detection benchmark. Times use this sender's monotonic clock.
"""
import argparse,asyncio,io,json,time
from pathlib import Path
import httpx,numpy as np,websockets
from PIL import Image

async def run(origin,duration,output):
    token=Path('data/runpod_app_token').read_text().strip()
    async with httpx.AsyncClient(base_url=origin,headers={'Authorization':'Bearer '+token},timeout=240) as client:
        health=(await client.get('/api/health')).json()
        if health['active_sources']:raise RuntimeError('An active source must not be interrupted')
        warm=await client.post('/api/models/moss/warm');warm.raise_for_status()
        async with httpx.AsyncClient(timeout=30) as public:
            image=await public.get('https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg')
        image.raise_for_status();picture=Image.open(io.BytesIO(image.content)).convert('RGB');picture.thumbnail((960,960))
        data=io.BytesIO();picture.save(data,format='JPEG',quality=72);jpeg=data.getvalue()
        response=await client.post('/api/sources',json={'name':'Public-image live-preview transport test','kind':'phone'})
        response.raise_for_status();pair=response.json();source_id=pair['source']['id']
        ws_origin=origin.replace('https://','wss://').replace('http://','ws://')
        acks=[];display=[];sent_times={};started=time.monotonic();first_send=None;stopping=False
        async with websockets.connect(ws_origin+'/ws/capture',max_size=2**22) as camera:
            await camera.send(json.dumps({'token':pair['capture_url'].split('#')[1]}))
            ready=json.loads(await camera.recv());assert ready['type']=='ready',ready
            async def receive_acks():
                async for raw in camera:
                    event=json.loads(raw)
                    if event['type']=='ack':acks.append((time.monotonic()-sent_times.pop(event['index']-1))*1000)
                    elif event['type']=='stopped':return
                    else:raise RuntimeError(str(event))
            async def preview():
                async with websockets.connect(ws_origin+'/ws/preview?source_id='+source_id,
                    additional_headers={'Cookie':'safesc_token='+token},origin=origin,max_size=2**22) as viewer:
                    try:
                        async for raw in viewer:
                            meta=json.loads(raw)
                            if meta['type']!='frame':continue
                            pixels=await viewer.recv();assert isinstance(pixels,bytes)
                            if meta.get('objects_frame_included'):await viewer.recv()
                            now=time.monotonic()
                            display.append({'timestamp':meta['timestamp'],'received_at':now-started,
                                'sender_to_preview_ms':(now-first_send-meta['timestamp'])*1000,
                                'preview_roundtrip_ms':meta['previous_roundtrip_ms']})
                            await viewer.send(json.dumps({'type':'displayed','index':meta['index']}))
                    except websockets.ConnectionClosed:
                        if not stopping:raise
            receiver=asyncio.create_task(receive_acks());viewer=asyncio.create_task(preview())
            index=0;deadline=time.monotonic()+duration
            try:
                while time.monotonic()<deadline:
                    if receiver.done():await receiver;raise RuntimeError('Camera stopped early')
                    if viewer.done():await viewer;raise RuntimeError('Preview stopped early')
                    now=time.monotonic()
                    if len(sent_times)<2:
                        if first_send is None:first_send=now
                        sent_times[index]=now
                        await camera.send(json.dumps({'type':'frame','timestamp':now-first_send}))
                        await camera.send(jpeg);index+=1
                    await asyncio.sleep(1/12)
                while sent_times:
                    if receiver.done():await receiver;raise RuntimeError('Camera stopped before acknowledging frames')
                    await asyncio.sleep(.02)
                stopping=True;await camera.send(json.dumps({'type':'stop'}))
                await asyncio.wait_for(receiver,180)
            finally:
                receiver.cancel();viewer.cancel();await asyncio.gather(receiver,viewer,return_exceptions=True)
        response=await client.get('/api/sources');response.raise_for_status()
        source=next(s for s in response.json() if s['id']==source_id)
    def stats(values):return {'median':float(np.median(values)),'p95':float(np.percentile(values,95))} if values else None
    result={'input':'Static public bus image, 960px JPEG quality 72, real models, max two capture frames in flight',
        'origin':origin,'source_id':source_id,'physical_phone_tested':False,'browser_paint_tested':False,
        'duration_seconds':duration,'sent_frames':index,'display_frames':len(display),
        'display_fps':(len(display)-1)/(display[-1]['received_at']-display[0]['received_at']) if len(display)>1 else 0,
        'capture_ack_ms':stats(acks),'sender_to_preview_ms':stats([d['sender_to_preview_ms'] for d in display]),
        'preview_roundtrip_ms':stats([d['preview_roundtrip_ms'] for d in display if d['preview_roundtrip_ms'] is not None]),
        'telemetry':source['telemetry'],'source_status':source['status']}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    assert len(display)>20 and source['telemetry']['frame_count']==index
    assert source['telemetry']['inference_frames']>0
    assert not source['telemetry'].get('error')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--origin',required=True);p.add_argument('--seconds',type=float,default=45)
    p.add_argument('--output',type=Path,default=Path('data/preview-smoke.json'));a=p.parse_args()
    asyncio.run(run(a.origin,a.seconds,a.output))
