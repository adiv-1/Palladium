"""Official BVI-Mamba offline comparison adapter; never resident during live monitoring."""
import os
import sys
from pathlib import Path
import cv2
import numpy as np

def prepare_inputs(frames,size):
    h,w=frames[-1].shape[:2];clip=list(frames[-5:])
    while len(clip)<5:clip.insert(0,clip[0])
    ratio=size/max(h,w);rh=max(1,round(h*ratio));rw=max(1,round(w*ratio))
    top=(size-rh)//2;left=(size-rw)//2
    x=np.stack([cv2.copyMakeBorder(cv2.resize(f,(rw,rh)),top,size-rh-top,
        left,size-rw-left,cv2.BORDER_REFLECT_101) for f in clip])
    return x.astype(np.float32)/127.5-1,(h,w,top,left,rh,rw)

def restore_output(output,shape):
    h,w,top,left,rh,rw=shape
    pixels=((np.clip(output,-1,1)+1)*127.5).round().astype(np.uint8)
    return cv2.resize(pixels[top:top+rh,left:left+rw],(w,h))

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
        torch=self.torch
        # Official inference uses BGR and [-1,1]. Preserve portrait aspect ratio.
        x,shape=prepare_inputs(frames,self.size)
        tensor=torch.from_numpy(x).permute(0,3,1,2).unsqueeze(0).cuda()
        with torch.inference_mode():result=self.net(tensor)[0]
        if not torch.isfinite(result).all():raise RuntimeError('BVI-Mamba returned non-finite pixels')
        return [restore_output(result.permute(1,2,0).cpu().numpy(),shape)]
