"""Real MOSS WebSocket smoke test on a public image; no incident fixture outputs."""
import asyncio
import io
import json
import os
import time
from pathlib import Path

import httpx
import websockets
from PIL import Image

async def main():
    root=Path('data/smoke');root.mkdir(parents=True,exist_ok=True)
    image=root/'bus.jpg'
    if not image.exists():
        response=httpx.get('https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg',timeout=30)
        response.raise_for_status();image.write_bytes(response.content)
    picture=Image.open(image).convert('RGB');picture.thumbnail((640,640))
    encoded=io.BytesIO();picture.save(encoded,format='JPEG')
    events=[];start=time.monotonic()
    url=os.getenv('MOSS_WS_URL','ws://127.0.0.1:18001/v1/realtime')
    async with httpx.AsyncClient(timeout=3) as client:
        for attempt in range(180):
            try:
                response=await client.get(url.replace('ws://','http://').split('/v1/')[0]+'/health')
                if response.is_success and response.json().get('model_loaded'):break
            except (httpx.HTTPError,ValueError):pass
            await asyncio.sleep(1)
        else:raise RuntimeError('MOSS failed to become healthy in 180 seconds')
    async with websockets.connect(url,max_size=2**22) as ws:
        await ws.send(json.dumps({'type':'start','system_prompt':'You are a realtime visual assistant. Describe only visible actions. Do not infer identity, demographics or intent.',
            'prompt':'Briefly describe the people and objects visible in these frames. Stay silent when nothing changes.',
            'frame_queue_size':1,'max_tokens_per_second':32,'max_new_tokens':512,'do_sample':False}))
        ready=json.loads(await asyncio.wait_for(ws.recv(),90));print(json.dumps(ready),flush=True)
        async def receive():
            async for raw in ws:
                event=json.loads(raw);event['received_seconds']=round(time.monotonic()-start,3);events.append(event)
                if event['type'] in ['output','error','session_end']: print(json.dumps(event),flush=True)
        receiver=asyncio.create_task(receive())
        for index in range(16):
            await ws.send(json.dumps({'type':'frame','timestamp':float(index)}));await ws.send(encoded.getvalue())
            if index==5:
                await ws.send(json.dumps({'type':'prompt','text':'Describe the most recent frame now in one short sentence. Mention visible objects only.'}))
            await asyncio.sleep(1)
        await asyncio.sleep(12)
        await ws.send(json.dumps({'type':'stop'}));await asyncio.sleep(1)
        receiver.cancel();await asyncio.gather(receiver,return_exceptions=True)
    result={'input':'Public Ultralytics bus image repeated as timestamped frames; static-input transport test',
        'simulated_inference':False,'runtime_seconds':time.monotonic()-start,'events':events}
    (root/'moss-realtime.json').write_text(json.dumps(result,indent=2))
    if any(e['type']=='error' for e in events):raise RuntimeError('MOSS returned an inference error')
    if not any(e['type']=='output' for e in events):raise RuntimeError('No model output received')

if __name__=='__main__':asyncio.run(main())
