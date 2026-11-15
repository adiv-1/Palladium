"""Bounded, colored ReLift export with exact input-frame timing and reprojection."""
import json
from pathlib import Path
import cv2
import numpy as np
from .video import Frame,encode,write_clip

def export_trajectories(npz,out,source_frames=None,motion_output=None,max_points=4500):
    with np.load(npz,allow_pickle=False) as data:
        dense=data['xyz'];t,h,w,_=dense.shape
        step=max(1,int(np.ceil(np.sqrt(h*w/max_points))))
        ys,xs=np.mgrid[step//2:h:step,step//2:w:step];ys=ys.ravel();xs=xs.ravel()
        xyz=dense[:,ys,xs].astype(np.float32)
        valid=np.isfinite(xyz).all(axis=2)
        observed=data['observed'][:,ys,xs].astype(bool) if 'observed' in data else valid.copy()
        observed &= valid
        colors=data['images'][0,ys,xs].tolist() if 'images' in data else [[99,221,191]]*len(xs)
        poses=data['poses'].astype(np.float64) if 'poses' in data else None
        intrinsics=data['intrinsics'].astype(np.float64) if 'intrinsics' in data else None
        frames=source_frames[:t] if source_frames else [{'index':i,'timestamp':i/15} for i in range(t)]
        if len(frames)!=t:raise ValueError('ReLift source-frame mapping does not match its output')
        if len(frames)>1 and any(b['timestamp']<=a['timestamp'] for a,b in zip(frames,frames[1:])):
            raise ValueError('ReLift source timestamps must increase')
        payload={'version':2,'simulated':False,'label':'ESTIMATED 3D POINT TRACKS',
            'units':'geometry-model units; not measured meters','coordinate_system':'frame-0 camera: x right, y down, z forward',
            'frame_indices':list(range(t)),'points':np.where(valid[...,None],xyz,0).round(5).tolist(),
            'colors':colors,'confidence':observed.astype(int).tolist(),
            'confidence_note':'Observed-by-tracker mask, not calibrated geometry accuracy. Invalid points are hidden.',
            'cameras':poses.tolist() if poses is not None else None,
            'intrinsics':intrinsics.tolist() if intrinsics is not None else None,
            'image_size':[w,h],'source_frames':frames,
            'quality':{'sampled_points':len(xs),'observed_fraction':observed.mean(axis=1).round(4).tolist(),
                'invalid_fraction':round(float(1-valid.mean()),5)},
            'purpose':'Inspect how estimated visible scene points move. This is not a body mesh, identity track or measured incident reconstruction.'}
        if motion_output and poses is not None and intrinsics is not None and 'images' in data:
            rendered=[];stride=max(1,len(xs)//650)
            for i in range(t):
                image=data['images'][i][...,::-1].copy()
                cam=xyz[i] @ poses[i,:3,:3].T+poses[i,:3,3]
                z=cam[:,2];uv=(cam @ intrinsics[i].T)
                uv=uv[:,:2]/np.where(z>1e-6,z,np.nan)[:,None]
                for j in range(0,len(xs),stride):
                    if observed[i,j] and np.isfinite(uv[j]).all() and z[j]>0:
                        x,y=np.rint(uv[j]).astype(int)
                        if 0<=x<w and 0<=y<h:
                            cv2.circle(image,(x,y),1,tuple(int(c) for c in colors[j][::-1]),-1,cv2.LINE_AA)
                rendered.append(Frame(float(frames[i]['timestamp']),i,encode(image)))
            write_clip(rendered,motion_output,'ESTIMATED MOTION TRACKS')
        Path(out).write_text(json.dumps(payload,separators=(',',':'),allow_nan=False))
        return payload['quality']
