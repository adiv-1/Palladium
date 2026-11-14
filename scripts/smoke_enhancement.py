"""Measure real FastLLVE and person tracking on a darkened public test image."""
import json
import time
from pathlib import Path
import cv2
import numpy as np
import torch
from ml.fastllve import FastLLVE
from ml.adapters import ByteTracker
from apps.api.video import digest

root=Path('data/smoke');root.mkdir(parents=True,exist_ok=True)
image=cv2.imread(str(root/'bus.jpg'))
if image is None:raise RuntimeError('Run smoke_realtime.py to fetch the public test image first')
image=cv2.resize(image,(640,360));dark=(image.astype(float)*.15).astype(np.uint8)
checkpoint=Path('weights/fastllve/FastLLVE_pretrain/FastLLVE_outdoor.pth')
torch.cuda.reset_peak_memory_stats();start=time.monotonic();model=FastLLVE(checkpoint)
output=model.enhance([dark]*7,{'state':'severely_dark'})[-1]
torch.cuda.synchronize();elapsed=time.monotonic()-start
cv2.imwrite(str(root/'fastllve-input.jpg'),dark);cv2.imwrite(str(root/'fastllve-output.jpg'),output)
metrics={'fastllve':{'status':'passed','checkpoint_sha256':digest(checkpoint),'first_call_seconds':elapsed,
    'input_mean':float(dark.mean()),'output_mean':float(output.mean()),'peak_vram_mb':torch.cuda.max_memory_allocated()/2**20}}
start=time.monotonic();tracker=ByteTracker();tracks=tracker.update(image,0)
torch.cuda.synchronize();metrics['tracking']={'status':'passed','first_call_seconds':time.monotonic()-start,'tracks':tracks,
    'checkpoint_sha256':digest(Path('yolo11n.pt'))}
(root/'enhancement-tracking.json').write_text(json.dumps(metrics,indent=2));print(json.dumps(metrics,indent=2))
assert output.shape==dark.shape and np.isfinite(output).all()
assert tracks,'No person tracks in the public person test image'
