"""Actual checkpoint test of the optional offline BVI-Mamba comparison path."""
import json
import time
from pathlib import Path
import cv2
import torch
from ml.bvimamba import BVIMamba
from apps.api.video import digest

root=Path('data/smoke');image=cv2.imread(str(root/'fastllve-input.jpg'))
if image is None:raise RuntimeError('FastLLVE public-image smoke input is missing')
checkpoint=Path('weights/bvimamba/BVIMamba_BVI.pth.tar')
started=time.monotonic();torch.cuda.reset_peak_memory_stats()
model=BVIMamba(checkpoint)
output=model.enhance([image]*5,{})[-1];torch.cuda.synchronize()
cv2.imwrite(str(root/'bvimamba-output.jpg'),output)
result={'model':'BVI-Mamba','status':'passed','runtime_seconds':time.monotonic()-started,
    'peak_vram_mb':torch.cuda.max_memory_allocated()/2**20,'checkpoint_sha256':digest(checkpoint),
    'output_sha256':digest(root/'bvimamba-output.jpg'),
    'settings':{'working_resolution':[512,512],'context_frames':5,'output_resized_to_original':True},
    'note':'Offline comparison smoke test on a darkened public image; not a quality benchmark.'}
(root/'bvimamba-metrics.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
