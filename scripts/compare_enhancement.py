"""Compare actually exported enhancement videos. Image metrics are not detection accuracy."""
import argparse
import json
import time
import cv2
import numpy as np
from apps.api.video import read_video, decode

def metrics(path):
    count=0;means=[];contrasts=[];started=time.monotonic()
    for frame in read_video(path):
        gray=cv2.cvtColor(decode(frame.jpeg),cv2.COLOR_BGR2GRAY)
        means.append(float(gray.mean()));contrasts.append(float(gray.std()));count+=1
    return {'frames':count,'mean_luminance':float(np.mean(means)),
        'mean_contrast':float(np.mean(contrasts)),
        'mean_absolute_brightness_change':float(np.mean(np.abs(np.diff(means)))) if len(means)>1 else None,
        'decode_fps':count/(time.monotonic()-started),
        'inference_fps':None,'peak_vram_mb':None,
        'note':'Brightness change is a flicker proxy confounded by scene motion. Decode FPS is not model FPS.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--original',required=True);p.add_argument('--fastllve',required=True);p.add_argument('--newer',required=True);a=p.parse_args()
    print(json.dumps({k:metrics(v) for k,v in vars(a).items()},indent=2))

if __name__=='__main__':main()
