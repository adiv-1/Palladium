"""Official FastLLVE network, conservative blending; requires compiled IA-LUT CUDA extension."""
import sys
from pathlib import Path
import numpy as np

class FastLLVE:
    def __init__(self,checkpoint,repo='third_party/FastLLVE'):
        import torch
        if not torch.cuda.is_available(): raise RuntimeError('FastLLVE requires the CUDA worker')
        if not Path(checkpoint).is_file(): raise RuntimeError('Install the official FastLLVE checkpoint first')
        sys.path.insert(0,str(Path(repo).resolve()))
        from models.IALUT.IALUT_model import IALUT_LLVE
        # Upstream init_weights references an undefined field. Setup script applies a documented fix.
        self.torch=torch
        self.net=IALUT_LLVE(input_resolution=[960,512],train_resolution=[960,512]).cuda().eval()
        state=torch.load(checkpoint,map_location='cpu',weights_only=True)
        state=state.get('state_dict',state)
        self.net.load_state_dict({k.removeprefix('module.'):v for k,v in state.items()},strict=True)
    def enhance(self,frames,metadata):
        import cv2
        torch=self.torch; h,w=frames[-1].shape[:2]
        clip=[cv2.resize(f,(960,512))[:,:,::-1].copy() for f in frames[-7:]]
        while len(clip)<7: clip.insert(0,clip[0])
        x=torch.from_numpy(np.stack(clip)).permute(3,0,1,2).unsqueeze(0).float().cuda()/255
        with torch.inference_mode(): result=self.net(x,if_train=False)[1]
        if not torch.isfinite(result).all():raise RuntimeError('FastLLVE returned non-finite pixels; original input retained')
        output=result[0].permute(1,2,3,0).clamp(0,1).cpu().numpy()
        enhanced=[cv2.resize((f[:,:,::-1]*255).astype(np.uint8),(w,h)) for f in output]
        alpha=.5 if metadata['state']=='moderately_dark' else 1
        return [cv2.addWeighted(frames[-1],1-alpha,enhanced[-1],alpha,0)]
