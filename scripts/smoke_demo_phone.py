"""Replay the three public demo originals through the actual paired-phone protocol.

No expected label enters inference, no manual bookmark creates a positive result,
and no hosted configuration is changed. Results distinguish receipt, model flags,
and clip preservation from a physical phone/browser test.
"""
import argparse
import asyncio
import hashlib
import json
import statistics
import time
from pathlib import Path

import httpx
import websockets

from apps.api.video import read_video


async def exercise(origin, token, output, only=None, tail=45):
    manifest = json.loads(Path('demo/videos/manifest.json').read_text())['files']
    reports = []
    async with httpx.AsyncClient(base_url=origin, headers={'Authorization': 'Bearer '+token}, timeout=240) as client:
        health = await client.get('/api/health'); health.raise_for_status()
        if health.json()['active_sources']:
            raise RuntimeError('An existing live source is active; refusing to interrupt it')
        if health.json()['simulated']:
            raise RuntimeError('This acceptance test requires real configured models')
        for item in manifest:
            if only and item['file']!=only:continue
            # Match the UI's prepare-before-capture flow. Existing background
            # work is allowed to finish; no job or model is reconfigured.
            deadline=time.monotonic()+300
            while True:
                warm=await client.post('/api/models/moss/warm')
                if warm.is_success:break
                if warm.status_code!=409 or time.monotonic()>deadline:
                    warm.raise_for_status()
                await asyncio.sleep(1)
            path = Path('demo/videos')/item['file']
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise RuntimeError('Public recording hash differs from manifest')
            frames = list(read_video(path))
            duration = frames[-1].timestamp
            # Original first/last frames provide quiet camera context before/after
            # the complete original action. No generated action or label prompts.
            lead = 8
            total = lead+duration+tail
            result = await client.post('/api/sources', json={'name': 'Demo acceptance · '+path.stem,
                'kind': 'phone', 'review_copies': False})
            result.raise_for_status(); pair = result.json(); source_id = pair['source']['id']
            started = time.monotonic(); pending = {}; ack_times = []; notices = {}; phone_notices = []
            url = origin.replace('https:', 'wss:').replace('http:', 'ws:')+'/ws/capture'
            print('Testing', path.name, 'through paired phone intake', flush=True)
            async with websockets.connect(url, max_size=2**22) as ws:
                await ws.send(json.dumps({'token': pair['capture_url'].split('#')[1]}))
                assert json.loads(await ws.recv())['type'] == 'ready'
                async def receive():
                    async for raw in ws:
                        message = json.loads(raw)
                        if message['type'] == 'ack':
                            ack_times.append((time.monotonic()-pending.pop(message['index']-1))*1000)
                        elif message['type'] == 'phone_alert':
                            phone_notices.append({'received_seconds':time.monotonic()-started, **message['alert']})
                        elif message['type'] == 'stopped':
                            return
                        elif message['type'] == 'error':
                            raise RuntimeError(message.get('message'))
                async def monitor():
                    while True:
                        response = await client.get('/api/sources');response.raise_for_status()
                        source = next(s for s in response.json() if s['id'] == source_id)
                        for notice in source['telemetry'].get('review_alerts', []):
                            if notice['id'] not in notices:
                                notices[notice['id']] = {'first_received_seconds': time.monotonic()-started, **notice}
                                print('Automatic flag after',round(time.monotonic()-started,2),'seconds;',notice['status'],flush=True)
                            else:
                                notices[notice['id']].update(notice)
                        await asyncio.sleep(.5)
                receiver=asyncio.create_task(receive()); watcher=asyncio.create_task(monitor()); index=0
                try:
                    while (elapsed:=time.monotonic()-started)<total:
                        if receiver.done():
                            await receiver
                            raise RuntimeError('Capture closed early')
                        if len(pending)<2:
                            t=min(duration,max(0,elapsed-lead))
                            frame=min(frames,key=lambda frame:abs(frame.timestamp-t))
                            pending[index]=time.monotonic()
                            await ws.send(json.dumps({'type':'frame','timestamp':elapsed}))
                            await ws.send(frame.jpeg);index+=1
                        await asyncio.sleep(1/12)
                    deadline=time.monotonic()+20
                    while pending and time.monotonic()<deadline:
                        if receiver.done():await receiver;raise RuntimeError('Capture ended before acknowledgements')
                        await asyncio.sleep(.02)
                    if pending:raise RuntimeError('Frame acknowledgement timeout')
                    await ws.send(json.dumps({'type':'stop'}));await asyncio.wait_for(receiver,240)
                finally:
                    receiver.cancel();watcher.cancel();await asyncio.gather(receiver,watcher,return_exceptions=True)
            source=next(s for s in (await client.get('/api/sources')).json() if s['id']==source_id)
            incidents=[i for i in (await client.get('/api/incidents')).json() if i['source_id']==source_id]
            preserved=[]
            for incident in incidents:
                assert not incident['simulated'] and incident['record']['trigger_origin']=='model'
                response=await client.get(f"/api/incidents/{incident['id']}/media/original");response.raise_for_status()
                assert hashlib.sha256(response.content).hexdigest()==incident['record']['original_sha256']
                preserved.append({'id':incident['id'],'start':incident['start_time'],'end':incident['end_time'],
                    'action':incident['record'].get('action'),'original_hash_verified':True})
            reports.append({'file':path.name,'sha256':item['sha256'],'source_id':source_id,
                'expected_reviewed_action':item['reviewed_action'],
                'acceptance_passed':not preserved if item['reviewed_action']=='ordinary_activity' else any(i['action']==item['reviewed_action'] for i in preserved),
                'original_action_start_seconds':lead,'original_duration_seconds':duration,'frames_acknowledged':len(ack_times),
                'mean_ack_ms':statistics.mean(ack_times),'p95_ack_ms':sorted(ack_times)[int((len(ack_times)-1)*.95)],
                'max_ack_ms':max(ack_times),'runtime_seconds':time.monotonic()-started,
                'alerts':list(notices.values()),'phone_alerts':phone_notices,'incidents':preserved,
                'model_error':source['telemetry'].get('error'),
                'verification_summaries':source['telemetry'].get('verification_windows',[])})
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps({'physical_phone_tested':False,'simulated_inference':False,
                'method':'Complete checked-in public originals over real-time phone JPEG protocol with static lead and tail context; labels never sent to models',
                'results':reports},indent=2))
            print(json.dumps({k:v for k,v in reports[-1].items() if k not in ['alerts','phone_alerts','verification_summaries']}),flush=True)
            if reports[-1]['model_error']:raise RuntimeError('Backend model error; see private local report')
        if not all(r['acceptance_passed'] for r in reports):raise RuntimeError('A demo outcome did not match its reviewed action; inspect the recorded diagnostic report')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--token-file',type=Path,default=Path('data/runpod_app_token'))
    parser.add_argument('--output',type=Path,default=Path('data/demo-phone-acceptance.json'))
    parser.add_argument('--only',choices=['quiet-scene.mp4','staged-contact-and-fall.mp4','staged-jacket-taking.mp4'])
    parser.add_argument('--tail-seconds',type=int,default=45)
    args=parser.parse_args()
    if not 10<=args.tail_seconds<=120:parser.error('tail must be between 10 and 120 seconds')
    asyncio.run(exercise(args.origin,args.token_file.read_text().strip(),args.output,args.only,args.tail_seconds))

if __name__=='__main__':main()
