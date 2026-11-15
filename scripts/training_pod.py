"""Bounded temporary training Pod; provider keys remain on the Mac."""
import argparse
import json
import subprocess
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
RECORD=ROOT/'data/training-pod.json'

def client():
    return httpx.Client(base_url='https://api.runpod.io/v2',headers={'Authorization':'Bearer '+dotenv_values(ROOT/'.env')['RUNPOD_TOKEN']},timeout=90)

def record(data):
    RECORD.write_text(json.dumps(data,indent=2));RECORD.chmod(0o600)

def ssh_args():
    pod=json.loads(RECORD.read_text());direct=pod['ssh']['direct']
    return ['ssh','-i',str(ROOT/'data/deploy_key'),'-p',str(direct['port']),'-o','BatchMode=yes','-o','ConnectTimeout=20','-o','ServerAliveInterval=20','-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile={ROOT}/data/runpod_known_hosts',f"{direct['username']}@{direct['host']}"]

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['create','status','terminate','guard']);a=p.parse_args()
    with client() as c:
        if a.action=='create':
            existing=c.get('/pods');existing.raise_for_status()
            pods=existing.json()['pods']
            if any(x.get('name')=='safesc-training' for x in pods):raise SystemExit('Training Pod already exists; inspect it instead')
            q=c.get('/catalog/gpus/NVIDIA%20A40',params={'include':'AVAILABILITY','product':'POD','count':1,'cloud':'SECURE','minCudaVersion':'12.8'});q.raise_for_status();gpu=q.json()
            if gpu['price']['secure']>.60:raise SystemExit('Compute quote exceeds $0.60/hour')
            locations=[d['id'] for d in gpu['dataCenters'] if d.get('availability') in ['LOW','MEDIUM','HIGH']]
            if not locations:raise SystemExit('No A40 capacity')
            r=c.post('/pods',json={'name':'safesc-training','cloud':'SECURE','dataCenterIds':locations,
                'image':'runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404',
                'gpu':{'id':'NVIDIA A40','count':1,'minCudaVersion':'12.8','minRamPerGpu':48,'minVcpuCountPerGpu':8},
                'disk':40,'mounts':{'persistent':{'size':80,'path':'/workspace'}},'ports':['22/tcp'],
                'startSsh':True,'startJupyter':False,'env':{'PUBLIC_KEY':(ROOT/'data/deploy_key.pub').read_text().strip()}})
            r.raise_for_status();pod=r.json();pod['training_deadline']=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat();record(pod)
            print('Created temporary A40 training Pod',pod['id'],'at quoted compute',gpu['price']['secure'],'USD/hour',flush=True)
            return
        pod=json.loads(RECORD.read_text());pod_id=pod['id']
        if a.action=='guard':
            deadline=datetime.fromisoformat(pod['training_deadline'])
            while datetime.now(timezone.utc)<deadline:time.sleep(30)
            # Hard local deadline, independent of training completion. Outputs must
            # be checkpointed and copied out before the temporary Pod expires.
            r=c.post(f'/pods/{pod_id}/action',json={'action':'terminate'});r.raise_for_status();print('Terminated training Pod at its 3-hour deadline',flush=True);return
        if a.action=='terminate':
            r=c.post(f'/pods/{pod_id}/action',json={'action':'terminate'});r.raise_for_status();print('Terminated temporary training Pod');return
        r=c.get(f'/pods/{pod_id}');r.raise_for_status();current=r.json();current['training_deadline']=pod['training_deadline'];record(current)
        print(json.dumps({k:current.get(k) for k in ['id','name','status','ssh','training_deadline']},indent=2))

if __name__=='__main__':main()
