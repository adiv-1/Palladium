"""Record installed sources and downloaded checkpoint identities on the worker."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

lock={}
for name,folder in [('moss','MOSS-VL'),('fastllve','FastLLVE'),('relayvsr','RelayVSR'),('relift','ReLift'),('bvimamba','BVI-Mamba')]:
    repo=ROOT/'third_party'/folder
    lock[name]={'repository':subprocess.check_output(['git','remote','get-url','origin'],cwd=repo,text=True).strip(),
        'source_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()}
lock['moss'].update(weights_repository='OpenMOSS-Team/MOSS-VL-Realtime-NF4',
    weights_revision=(ROOT/'weights/moss-nf4/.cache/huggingface/download/config.json.metadata').read_text().splitlines()[0])
relay_weights=json.loads((ROOT/'third_party/RelayVSR/weights/provenance.json').read_text())
lock['relayvsr'].update(weights_repository=relay_weights['repository'],weights_revision=relay_weights['revision'])
for name,path in [('fastllve','weights/fastllve/FastLLVE_pretrain/FastLLVE_outdoor.pth'),
    ('bvimamba','weights/bvimamba/BVIMamba_BVI.pth.tar'),('tracking','yolo11n.pt'),
    ('relift','third_party/ReLift/checkpoints/released_checkpoint.pt')]:
    lock.setdefault(name,{})['checkpoint_sha256']=sha(ROOT/path)
    lock[name]['checkpoint_path']=path
lock['relift']['dependencies']={
    'da3':{'repository':'depth-anything/DA3NESTED-GIANT-LARGE-1.1','revision':'b2359bdf726fb44ef62acca04d629dcf158053e7','license':'CC-BY-NC-4.0'},
    'alltracker':{'repository':'aharley/alltracker','license':'MIT'},
    'vggt_omega':{'installed':False,'note':'Optional gated backend; unused by the DA3/AllTracker pipeline'},
    'cowtracker':{'installed':False,'note':'Optional backend; unused by the DA3/AllTracker pipeline'}}
for name,path in [('fastllve','data/smoke/enhancement-tracking.json'),('relayvsr','data/smoke/relayvsr-metrics.json'),
    ('relift','data/smoke/relift-metrics.json'),('bvimamba','data/smoke/bvimamba-metrics.json')]:
    if (ROOT/path).exists():lock[name]['smoke_test']=json.loads((ROOT/path).read_text())
moss_result=ROOT/'data/smoke/moss-realtime.json'
events=json.loads(moss_result.read_text()).get('events',[]) if moss_result.exists() else []
passed=any(e.get('type')=='output' for e in events) and not any(e.get('type')=='error' for e in events)
lock['moss']['smoke_test']={'path':'data/smoke/moss-realtime.json','status':'passed' if passed else 'not_verified','scope':'Timestamped frame transport, native silence, prompted visible-object description'}
verifier=ROOT/'weights/qwen3-vl-4b/.cache/huggingface/download/config.json.metadata'
if verifier.exists():
    lock['verifier']={'weights_repository':'Qwen/Qwen3-VL-4B-Instruct','weights_revision':verifier.read_text().splitlines()[0],
        'license':'Apache-2.0','transformers':'4.57.1','lm_format_enforcer':'0.11.3'}
    report=ROOT/'data/smoke/verifier-metrics.json'
    if report.exists():lock['verifier']['smoke_test']=json.loads(report.read_text())
(ROOT/'model-lock.json').write_text(json.dumps(lock,indent=2)+'\n')
print(json.dumps(lock,indent=2))
