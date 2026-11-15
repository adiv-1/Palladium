from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import math
import av
import cv2
import numpy as np

@dataclass
class Frame:
    timestamp: float
    index: int
    jpeg: bytes
    motion: float = 0

def select_evidence(frames,count=8):
    """Select real context and motion transitions; this never classifies an event."""
    if len(frames)<=count:return list(frames)
    if count==4 and max(f.motion for f in frames)>0:
        peak=max(range(1,len(frames)),key=lambda i:frames[i].motion)
        at=frames[peak].timestamp
        selected={min(range(len(frames)),key=lambda i:abs(frames[i].timestamp-t)) for t in [at-.5,at-.15,at,at+.5]}
        for i in np.linspace(0,len(frames)-1,4).round().astype(int):
            if len(selected)<4:selected.add(int(i))
        return [frames[i] for i in sorted(selected)]
    chosen={0,len(frames)-1};peaks=[]
    for index in sorted(range(1,len(frames)),key=lambda i:frames[i].motion,reverse=True):
        if any(abs(frames[index].timestamp-t)<.7 for t in peaks):continue
        peaks.append(frames[index].timestamp)
        for stamp in [frames[index].timestamp-.3,frames[index].timestamp,frames[index].timestamp+.3]:
            nearest=min(range(len(frames)),key=lambda i:abs(frames[i].timestamp-stamp))
            if len(chosen)<count:chosen.add(nearest)
        if len(chosen)>=count:break
    if len(chosen)<count:
        for i in np.linspace(0,len(frames)-1,count).round().astype(int):
            if len(chosen)<count:chosen.add(int(i))
    return [frames[i] for i in sorted(chosen)]

def digest(path):
    h=sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

class CircularBuffer:
    def __init__(self, seconds=22, max_bytes=128*1024*1024):
        self.seconds=seconds; self.max_bytes=max_bytes; self.frames=deque(); self.size=0
    def append(self,frame):
        if not math.isfinite(frame.timestamp):
            raise ValueError('Frame timestamp must be finite')
        if self.frames and frame.timestamp <= self.frames[-1].timestamp:
            raise ValueError('Frame timestamps must increase strictly')
        self.frames.append(frame); self.size+=len(frame.jpeg)
        while self.frames and (frame.timestamp-self.frames[0].timestamp>self.seconds or self.size>self.max_bytes):
            self.size-=len(self.frames.popleft().jpeg)
    def window(self,start,end): return [f for f in self.frames if start<=f.timestamp<=end]

class LightingGate:
    def __init__(self,dark=45,light=70):
        self.dark=dark; self.light=light; self.values=deque(maxlen=8); self.state='well_lit'
    def update(self,image):
        y=cv2.cvtColor(image,cv2.COLOR_BGR2YCrCb)[:,:,0]
        median=float(np.median(y)); self.values.append(median)
        rolling=float(np.median(self.values))
        if len(self.values)>=3:
            if rolling<self.dark-15: self.state='severely_dark'
            elif rolling<self.dark: self.state='moderately_dark'
            elif rolling>self.light: self.state='well_lit'
        return {'state':self.state,'median_luminance':median,'rolling_luminance':rolling,
                'dark_fraction':float(np.mean(y<30)),'highlight_clipping':float(np.mean(y>245)),
                'contrast':float(y.std()),'stability':float(np.std(self.values))}

def decode(jpeg):
    im=cv2.imdecode(np.frombuffer(jpeg,np.uint8),cv2.IMREAD_COLOR)
    if im is None or im.shape[0]*im.shape[1]>4096*2160: raise ValueError('Invalid or oversized image')
    return im

def encode(image): return cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,85])[1].tobytes()

def write_clip(frames,path,label=None):
    """Timestamp-preserving H.264 derivative; never overwrites any existing artifact."""
    if not frames: raise ValueError('No frames in incident window')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with open(path,'xb') as raw:
        with av.open(raw,mode='w',format='mp4',options={'movflags':'+faststart'}) as out:
            image=decode(frames[0].jpeg); h,w=image.shape[:2]
            stream=out.add_stream('libx264',rate=30); stream.width=w//2*2; stream.height=h//2*2
            stream.pix_fmt='yuv420p'; stream.options={'crf':'18','preset':'fast'}; stream.time_base=Fraction(1,90000)
            stream.codec_context.time_base=Fraction(1,90000)
            for f in frames:
                im=decode(f.jpeg)
                if label:
                    scale=max(.5,min(im.shape[1]/900,2.5));height=round(44*scale)
                    cv2.rectangle(im,(0,0),(im.shape[1],height),(15,15,15),-1)
                    cv2.putText(im,label,(round(12*scale),round(29*scale)),cv2.FONT_HERSHEY_SIMPLEX,scale,(255,255,255),max(1,round(scale)))
                v=av.VideoFrame.from_ndarray(im,format='bgr24'); v=v.reformat(width=stream.width,height=stream.height)
                v.pts=round((f.timestamp-frames[0].timestamp)*90000); v.time_base=Fraction(1,90000)
                for packet in stream.encode(v): out.mux(packet)
            for packet in stream.encode(): out.mux(packet)
    Path(path).chmod(0o444)

def read_video(path):
    with av.open(str(path)) as container:
        stream=container.streams.video[0]
        for index,frame in enumerate(container.decode(stream)):
            ts=float(frame.time) if frame.time is not None else index/float(stream.average_rate or 30)
            yield Frame(ts,index,encode(frame.to_ndarray(format='bgr24')))
