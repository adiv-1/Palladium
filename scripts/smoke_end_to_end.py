"""Exercise upload -> human bookmark -> real queued artifacts -> audited review.

The bookmark is deliberately human-originated. This test makes no positive AI
incident-detection claim. Only the official ReLift public demo clip is uploaded.
"""
import asyncio
import hashlib
import io
import json
import os
import time
from pathlib import Path
import httpx
import websockets
from PIL import Image

ORIGIN=os.getenv('SAFESC_TEST_ORIGIN','http://127.0.0.1:8000')

async def main():
    folder=Path('data/end-to-end');folder.mkdir(parents=True,exist_ok=True)
    token=Path('data/runpod_app_token').read_text().strip()
    video=Path('data/upstream-cartwheel-test.mp4');expected=hashlib.sha256(video.read_bytes()).hexdigest()
    started=time.monotonic()
    async with httpx.AsyncClient(base_url=ORIGIN,headers={'Authorization':f'Bearer {token}'},timeout=30) as client:
        with video.open('rb') as stream:
            response=await client.post('/api/upload',files={'file':('upstream-cartwheel-engineering-test.mp4',stream,'video/mp4')})
        response.raise_for_status();source=response.json();source_id=source['id'];print('Uploaded official demo clip',source_id,flush=True)
        assert source['original_sha256']==expected
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            response=await client.get('/api/sources');response.raise_for_status()
            state=next(s for s in response.json() if s['id']==source_id)
            if state['status']=='streaming' and state['telemetry'].get('frame_count',0)>=3:
                response=await client.post(f'/api/sources/{source_id}/bookmark');response.raise_for_status();print('Human bookmark accepted',flush=True);break
            if state['status'] in ['failed','completed']:raise RuntimeError(f'Source finished before bookmark: {state}')
            await asyncio.sleep(.12)
        else:raise RuntimeError('Upload never became ready')
        incident=None
        while time.monotonic()<deadline:
            response=await client.get('/api/incidents');response.raise_for_status()
            incident=next((i for i in response.json() if i['source_id']==source_id and i['record'].get('trigger_origin')=='reviewer'),None)
            if incident:break
            await asyncio.sleep(.5)
        if not incident:raise RuntimeError('Bookmark did not produce a reviewable clip')
        incident_id=incident['id'];print('Review clip created',incident_id,flush=True)
        response=await client.get(f'/api/incidents/{incident_id}/media/original');response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest()==incident['record']['original_sha256']
        (folder/'original.mp4').write_bytes(response.content)
        response=await client.post(f'/api/incidents/{incident_id}/compare');response.raise_for_status();comparison=response.json()
        response=await client.post(f'/api/incidents/{incident_id}/compare');response.raise_for_status();assert response.json()['id']==comparison['id']
        # Prove the included CPU keeps phone intake online during a real heavy GPU job.
        response=await client.post('/api/sources',json={'name':'Phone intake during GPU job - engineering test','kind':'phone'})
        response.raise_for_status();pair=response.json();pair_token=pair['capture_url'].split('#')[1]
        image=Image.new('RGB',(320,180),(70,80,90));encoded=io.BytesIO();image.save(encoded,format='JPEG')
        acks=[]
        async with websockets.connect(ORIGIN.replace('https:','wss:')+'/ws/capture') as ws:
            await ws.send(json.dumps({'token':pair_token}));assert json.loads(await ws.recv())['type']=='ready'
            for index in range(6):
                sent=time.monotonic();await ws.send(json.dumps({'type':'frame','timestamp':index*.25}));await ws.send(encoded.getvalue())
                ack=json.loads(await asyncio.wait_for(ws.recv(),5));assert ack['type']=='ack';acks.append({**ack,'roundtrip_ms':(time.monotonic()-sent)*1000})
                await asyncio.sleep(.25)
            await ws.send(json.dumps({'type':'stop'}));assert json.loads(await asyncio.wait_for(ws.recv(),220))['type']=='stopped'
        print('Phone intake stayed online during queued GPU processing',flush=True)
        previous=None;deadline=time.monotonic()+780
        while time.monotonic()<deadline:
            response=await client.get(f'/api/incidents/{incident_id}');response.raise_for_status();detail=response.json()
            summary=[(j['kind'],j['status']) for j in detail['jobs']]
            if summary!=previous:print('Job states',summary,flush=True);previous=summary
            if any(j['status']=='failed' for j in detail['jobs']):raise RuntimeError(json.dumps(detail['jobs'],indent=2))
            if len(detail['jobs'])>=3 and all(j['status']=='completed' for j in detail['jobs']):break
            await asyncio.sleep(2)
        else:raise RuntimeError('Timed out waiting for real GPU artifacts')
        for kind in ['review','trajectory_original','bvimamba_review']:
            response=await client.get(f'/api/incidents/{incident_id}/media/{kind}');response.raise_for_status()
            (folder/({'review':'relay-review.mp4','trajectory_original':'trajectories.json','bvimamba_review':'bvi-review.mp4'}[kind])).write_bytes(response.content)
        trajectory=json.loads((folder/'trajectories.json').read_text());assert trajectory['simulated'] is False and len(trajectory['points'])>=2
        for decision in ['needs_review','dismissed']:
            response=await client.post(f'/api/incidents/{incident_id}/review',json={'decision':decision,'note':'Engineering test on official upstream cartwheel demo; human bookmark, not an AI incident.'})
            response.raise_for_status()
        response=await client.get(f'/api/incidents/{incident_id}');response.raise_for_status();detail=response.json()
        assert len(detail['history'])>=3 and detail['incident']['review_status']=='dismissed'
        assert detail['incident']['record']['source_sha256']==expected
        response=await client.get('/api/reviews/export');response.raise_for_status();assert response.json()['automatic_training'] is False
        result={'status':'passed','runtime_seconds':time.monotonic()-started,'source_id':source_id,'incident_id':incident_id,
            'trigger_origin':'reviewer','automatic_detection_tested':False,'source_sha256':expected,'detail':detail,
            'phone_during_gpu_job':acks,'physical_phone_tested':False}
        (folder/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['detail','phone_during_gpu_job']},indent=2),flush=True)

if __name__=='__main__':asyncio.run(main())
