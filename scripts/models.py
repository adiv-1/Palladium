"""Fetch pinned upstream sources and model snapshots, recording actual revisions."""
import argparse
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
SOURCES={
    'moss':('OpenMOSS/MOSS-VL','MOSS-VL','a3feb74da1a5184bfc10be59b58a33874231f51e'),
    'fastllve':('Wenhao-Li-777/FastLLVE','FastLLVE','6ac2629aaafbab87789df2410bb783871dca6b68'),
    'relayvsr':('kopperx/RelayVSR','RelayVSR',None),
    'relift':('ReLift3D/ReLift','ReLift',None),
    'bvimamba':('edhuang1/BVI-Mamba','BVI-Mamba',None),
}
def run(*args): return subprocess.check_output(args,text=True).strip()

def main():
    p=argparse.ArgumentParser();p.add_argument('model',choices=[*SOURCES,'all']);p.add_argument('--weights',action='store_true');a=p.parse_args()
    lockpath=Path('model-lock.json');lock=json.loads(lockpath.read_text()) if lockpath.exists() else {}
    for name in SOURCES if a.model=='all' else [a.model]:
        repository,directory,pin=SOURCES[name];target=Path('third_party')/directory
        if not target.exists(): run('git','clone','--filter=blob:none',f'https://github.com/{repository}.git',str(target))
        revision=pin or lock.get(name,{}).get('source_revision') or run('git','-C',str(target),'rev-parse','HEAD')
        run('git','-C',str(target),'checkout',revision)
        entry={**lock.get(name,{}),'repository':f'https://github.com/{repository}','source_revision':revision}
        if name=='relift': run('git','-C',str(target),'submodule','update','--init','--recursive')
        if a.weights and name in ['moss','relayvsr']:
            from huggingface_hub import HfApi, snapshot_download
            repo='OpenMOSS-Team/MOSS-VL-Realtime-NF4' if name=='moss' else 'kopper/RelayVSR'
            sha=lock.get(name,{}).get('weights_revision') or HfApi().model_info(repo).sha
            destination=Path('weights')/'moss-nf4' if name=='moss' else target/'weights'
            snapshot_download(repo,revision=sha,local_dir=destination)
            entry.update({'weights_repository':repo,'weights_revision':sha})
        # A different model download may have finished while this one fetched weights.
        lock=json.loads(lockpath.read_text()) if lockpath.exists() else {}
        lock[name]=entry
        lockpath.write_text(json.dumps(lock,indent=2)+'\n')
    print('Revisions recorded in model-lock.json. Model inference has NOT been run.')

if __name__=='__main__':main()
