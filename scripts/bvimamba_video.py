"""Bounded incident-clip comparison using the official centered five-frame model."""
import argparse
import json
import time
from pathlib import Path
from ml.bvimamba import BVIMamba
from apps.api.video import Frame,read_video,encode,write_clip,digest,decode
import cv2
import numpy as np

def main():
    parser=argparse.ArgumentParser();parser.add_argument('input');parser.add_argument('output');args=parser.parse_args()
    started=time.monotonic();frames=list(read_video(args.input))
    if not frames:raise ValueError('Empty comparison input')
    model=None;output=[];applied=0;guarded=0;metrics=[]
    for index,frame in enumerate(frames):
        context=[decode(frames[min(max(index+offset,0),len(frames)-1)].jpeg) for offset in [-2,-1,0,1,2]]
        original=context[2];y=cv2.cvtColor(original,cv2.COLOR_BGR2GRAY)
        median=float(np.median(y));alpha=float(np.clip((70-median)/35,0,1))
        result=original
        if alpha>0:
            if model is None:model=BVIMamba('weights/bvimamba/BVIMamba_BVI.pth.tar')
            candidate=model.enhance(context,{})[0]
            cy=cv2.cvtColor(candidate,cv2.COLOR_BGR2GRAY)
            if float(np.mean(cy>245))>float(np.mean(y>245))+.12:
                alpha=min(alpha,.35);guarded+=1
            result=cv2.addWeighted(candidate,alpha,original,1-alpha,0);applied+=1
        metrics.append({'frame':frame.index,'source_median':median,'enhancement_blend':round(alpha,3)})
        output.append(Frame(frame.timestamp,frame.index,encode(result)))
    write_clip(output,args.output,'BVI-MAMBA LOW-LIGHT REVIEW' if applied else 'ORIGINAL EXPOSURE - ENHANCEMENT NOT NEEDED')
    record={'model':'BVI-Mamba','input_sha256':digest(args.input),'output_sha256':digest(args.output),
        'runtime_seconds':time.monotonic()-started,'working_resolution':[512,512],
        'context_frames':5,'alignment':'Centered on each output frame; replicated boundary padding',
        'resizing':'Aspect-preserving letterbox; restored original dimensions and timestamps',
        'adapter_version':2,'color_space':'BGR','normalization':'input/output [-1,1], official inference convention',
        'status':'applied' if applied else 'not_applicable','applied_frames':applied,'total_frames':len(frames),
        'highlight_guard_frames':guarded,'frame_metrics':metrics,
        'reason':'Low-light-only enhancement with conservative highlight guard' if applied else 'Source is already well lit; exposure preserved'}
    Path(str(args.output)+'.json').write_text(json.dumps(record,indent=2));print(json.dumps(record),flush=True)

if __name__=='__main__':main()
