"""Official BVI-Mamba offline comparison adapter; never resident during live monitoring."""
import os
import sys
from pathlib import Path
import cv2
import numpy as np

class BVIMamba:
    def __init__(self,checkpoint,repo='third_party/BVI-Mamba',size=512):
        import torch
        import yaml
        os.environ.setdefault('BASICSR_JIT','True')
        os.environ.setdefault('TORCH_EXTENSIONS_DIR','/workspace/.cache/torch_extensions')
        os.environ.setdefault('TORCH_CUDA_ARCH_LIST','8.6')
        sys.path.insert(0,str(Path(repo).resolve()))
        from arch.BVIMamba import BVIMamba as Network
        config=yaml.safe_load((Path(repo)/'configs/BVIMamba.yml').read_text())
        kwargs={k:v for k,v in config['model'].items() if k not in ['network']}
        self.size=size;self.torch=torch
        self.net=Network(**kwargs,num_frame=config['dataset']['num_frames'],img_size=[size,size],final_upsample='Dual up-sample').cuda().eval()
        state=torch.load(checkpoint,map_location='cpu',weights_only=True)
        state=state.get('state_dict',state)
        self.net.load_state_dict({k.removeprefix('module.'):v for k,v in state.items()},strict=True)
    def enhance(self,frames,metadata):
        torch=self.torch;h,w=frames[-1].shape[:2]
        clip=list(frames[-5:])
        while len(clip)<5:clip.insert(0,clip[0])
        x=np.stack([cv2.resize(f,(self.size,self.size))[:,:,::-1].copy() for f in clip])
        tensor=torch.from_numpy(x).permute(0,3,1,2).unsqueeze(0).float().cuda()/255
        with torch.inference_mode():result=self.net(tensor)[0]
        if not torch.isfinite(result).all():raise RuntimeError('BVI-Mamba returned non-finite pixels')
        output=result.clamp(0,1).permute(1,2,0).cpu().numpy()
        return [cv2.resize((output[:,:,::-1]*255).astype(np.uint8),(w,h))]
