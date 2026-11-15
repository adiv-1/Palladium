"""Original-clip secondary visual pass; isolated worker exits to release CUDA memory."""
import argparse,json
from pathlib import Path
import numpy as np
from apps.api.config import settings
from apps.api.video import read_video,digest
from ml.clip_review import review_clip

def main():
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output');parser.add_argument('--initial',required=True);a=parser.parse_args()
    frames=list(read_video(a.input))
    indices=np.unique(np.linspace(0,len(frames)-1,min(8,len(frames))).round().astype(int))
    report=review_clip(settings.verifier_checkpoint,[frames[i] for i in indices],json.loads(Path(a.initial).read_text()),audit_path=str(a.output)+'.raw.json')
    report['input_sha256']=digest(a.input)
    with Path(a.output).open('x') as f:json.dump(report,f,indent=2,allow_nan=False)
    print(json.dumps({k:v for k,v in report.items() if k not in ['raw_json','report']}),flush=True)
if __name__=='__main__':main()
