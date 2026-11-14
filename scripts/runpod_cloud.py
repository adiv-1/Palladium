"""Runpod REST v2 deployment. No billing settings or account SSH keys are changed."""
import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
API = 'https://api.runpod.io/v2'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deploy', action='store_true')
    args = parser.parse_args()
    token = dotenv_values(ROOT / '.env').get('RUNPOD_TOKEN')
    if not token:
        raise SystemExit('RUNPOD_TOKEN is missing')
    with httpx.Client(base_url=API, headers={'Authorization': f'Bearer {token}'}, timeout=90) as client:
        response = client.get('/pods'); response.raise_for_status()
        existing = [p for p in response.json()['pods'] if p.get('name') == 'safesc-gpu']
        if existing:
            pod = existing[0]
            print(json.dumps({k: pod.get(k) for k in ['id','name','status','cost','dataCenterId','gpu','ssh']}, indent=2))
            (ROOT / 'data/runpod-deployment.json').write_text(json.dumps(pod, indent=2))
            (ROOT / 'data/runpod-deployment.json').chmod(0o600)
            return
        response = client.get('/catalog/gpus/NVIDIA%20A40', params={
            'include':'AVAILABILITY','product':'POD','count':1,'cloud':'SECURE','minCudaVersion':'12.8'})
        response.raise_for_status()
        gpu = response.json()
        if gpu['price']['secure'] > .60:
            raise SystemExit('A40 price exceeds authorized configuration cap')
        print(json.dumps(gpu, indent=2))
        if not args.deploy:
            return
        key = ROOT / 'data/deploy_key'
        if not key.exists():
            subprocess.run(['ssh-keygen','-t','ed25519','-N','','-C','safesc-deploy','-f',str(key)], check=True, stdout=subprocess.DEVNULL)
        available = {d['id'] for d in gpu.get('dataCenters',[]) if d.get('availability') in ['LOW','MEDIUM','HIGH']}
        candidates = [dc for dc in ['CA-MTL-1','EU-SE-1'] if dc in available]
        if not candidates:
            raise SystemExit('No acceptable A40 stock; nothing created')
        for dc in candidates:
            request = {
                'name':'safesc-gpu','cloud':'SECURE','dataCenterIds':[dc],
                'image':'runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404',
                'gpu':{'id':'NVIDIA A40','count':1,'minCudaVersion':'12.8','minRamPerGpu':48,'minVcpuCountPerGpu':8},
                'disk':50,'mounts':{'persistent':{'size':200,'path':'/workspace'}},
                'ports':['22/tcp','8000/http'],
                'startSsh':True,'startJupyter':False,
                'env':{'PUBLIC_KEY':key.with_suffix('.pub').read_text().strip()},
            }
            response = client.post('/pods', json=request)
            if response.status_code == 201:
                pod = response.json()
                pod['safesc_created_at'] = datetime.now(timezone.utc).isoformat()
                destination = ROOT / 'data/runpod-deployment.json'
                destination.write_text(json.dumps(pod, indent=2)); destination.chmod(0o600)
                print('CREATED', json.dumps({k:pod.get(k) for k in ['id','status','cost','dataCenterId','gpu','ssh']}))
                return
            print(dc, response.status_code, response.text[:1200])
            if response.status_code not in [400,403]:
                raise SystemExit('Stopped; deployment rejected; no further candidates attempted')
        raise SystemExit('All acceptable A40 placements failed')

if __name__ == '__main__':
    main()
