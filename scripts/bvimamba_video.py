"""Bounded incident-clip comparison using the official centered five-frame model."""
import argparse
import json
import time
from pathlib import Path
from ml.bvimamba import BVIMamba
from apps.api.video import Frame,read_video,encode,write_clip,digest,decode

def main():
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output');args=parser.parse_args()
    started=time.monotonic();frames=list(read_video(args.input))
    if not frames:raise ValueError('Empty comparison input')
    model=BVIMamba('weights/bvimamba/BVIMamba_BVI.pth.tar');output=[]
    for index,frame in enumerate(frames):
        context=[decode(frames[min(max(index+offset,0),len(frames)-1)].jpeg) for offset in [-2,-1,0,1,2]]
        result=model.enhance(context,{})[0]
        output.append(Frame(frame.timestamp,frame.index,encode(result)))
    write_clip(output,args.output,'AI-ENHANCED REVIEW COPY - BVI-MAMBA')
    record={'model':'BVI-Mamba','input_sha256':digest(args.input),'output_sha256':digest(args.output),
        'runtime_seconds':time.monotonic()-started,'working_resolution':[512,512],
        'context_frames':5,'alignment':'Centered on each output frame; replicated boundary padding',
        'resizing':'Restored to original dimensions after inference; original timestamps retained'}
    Path(str(args.output)+'.json').write_text(json.dumps(record,indent=2));print(json.dumps(record),flush=True)

if __name__=='__main__':main()
