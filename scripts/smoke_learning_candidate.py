"""Real adapter integration check with shifted frame IDs and timestamps.

Reuses two known evaluation imagesets: not an additional independent accuracy
benchmark. It validates the installed artifact and runtime evidence alignment.
"""
import json,time
from pathlib import Path
from types import SimpleNamespace
from apps.api.config import settings
from ml.verifier import acquire_verifier,release_verifier

def main():
    records=[json.loads(x) for x in Path('data/learning/pilot/records.jsonl').read_text().splitlines()] if Path('data/learning/pilot/records.jsonl').exists() else None
    root=Path('data/learning/pilot')
    if records is None:
        root=next(p.parent for p in Path('data/learning/datasets').glob('*/records.jsonl'))
        records=[json.loads(x) for x in (root/'records.jsonl').read_text().splitlines()]
    verifier=acquire_verifier(settings.verifier_checkpoint)
    if not verifier.adapter or not verifier.research_mode:raise RuntimeError('Expected the approved research adapter')
    results=[]
    for name in ['Stealing081_x264','Normal_Videos731_x264']:
        record=next(r for r in records if r['video_id']==name)
        frames=[SimpleNamespace(index=1000+i*8,timestamp=500+i*2,jpeg=(root/f).read_bytes()) for i,f in enumerate(record['frames'])]
        started=time.monotonic();detection=verifier.verify_frames(frames)
        result={'video':name,'expected':record['target']['action'],'result':detection.model_dump(),'runtime_seconds':time.monotonic()-started,'audit':verifier.last_audit}
        results.append(result);print(json.dumps({k:v for k,v in result.items() if k!='audit'}),flush=True)
        if detection.action!=record['target']['action']:raise RuntimeError('Adapter runtime action differs from the recorded integration expectation')
    Path('data/learning/runtime-smoke.json').write_text(json.dumps({'status':'passed','research_mode':True,'adapter_sha256':verifier.adapter,'results':results},indent=2))
    release_verifier()

if __name__=='__main__':main()
