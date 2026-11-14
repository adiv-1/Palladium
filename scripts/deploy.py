"""Deploy source + worker-only configuration to the exact recorded Vultr instance.

No Vultr administrative credential is copied to the worker. SSH trust is pinned on first contact
in the project-owned data/known_hosts file. Run cloud.py to check the instance identity first.
"""
import argparse
import json
import subprocess
import tarfile
import time
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--bootstrap',action='store_true');p.add_argument('--models',action='store_true');a=p.parse_args()
    meta=json.loads((ROOT/'data/deployment.json').read_text())
    if meta.get('label')!='safesc-gpu' or not meta.get('main_ip'): raise SystemExit('Verify instance ID and IP using cloud.py first')
    destination='root@'+meta['main_ip']
    ssh=['ssh','-i',str(ROOT/'data/deploy_key'),'-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile={ROOT}/data/known_hosts',destination]
    archive=ROOT/'data/source.tar.gz'
    def include(info):
        if any(x in ['node_modules','__pycache__','.pytest_cache','dist'] for x in Path(info.name).parts): return None
        return info
    with tarfile.open(archive,'w:gz') as tar:
        for name in ['apps','ml','scripts','docs','tests','patches','evaluation','pyproject.toml','uv.lock','model-lock.json','README.md','Dockerfile','compose.yaml','Caddyfile','.env.example','.gitignore']:
            path=ROOT/name
            if path.exists(): tar.add(path,arcname=name,filter=include)
    subprocess.run(ssh+['mkdir -p /opt/safesc'],check=True)
    with archive.open('rb') as file:
        subprocess.run(ssh+['tar -xzf - -C /opt/safesc'],stdin=file,check=True)
    credentials=dotenv_values(ROOT/'.env')
    env={'HF_TOKEN':credentials.get('HF_TOKEN',''),'PUBLIC_URL':credentials.get('PUBLIC_URL') or f"http://{meta['main_ip']}:8000",'STREAMING_MODEL':'moss'}
    content=''.join(f'{key}={json.dumps(value)}\n' for key,value in env.items())
    subprocess.run(ssh+['umask 077; tee /opt/safesc/.env >/dev/null'],input=content.encode(),check=True)
    if a.bootstrap:
        subprocess.run(ssh+['bash /opt/safesc/scripts/bootstrap_ubuntu.sh'],check=True)
    if a.models:
        subprocess.run(ssh+['cd /opt/safesc && bash scripts/setup_gpu.sh'],check=True)
    print('Source deployed. Worker configuration contains HF_TOKEN only; no Vultr key copied.')

if __name__=='__main__':main()
