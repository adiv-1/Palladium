"""Create a clearly synthetic engineering fixture, never a model benchmark."""
from pathlib import Path
import cv2
import numpy as np
from apps.api.video import Frame, encode, write_clip

def main():
    path=Path('data/demo-fixture.mp4');path.parent.mkdir(exist_ok=True)
    frames=[]
    for n in range(64):
        t=n/8;im=np.full((360,640,3),28 if t>4 else 90,np.uint8)
        cv2.putText(im,'SIMULATED ENGINEERING FIXTURE',(35,35),cv2.FONT_HERSHEY_SIMPLEX,.6,(240,230,180),1)
        cv2.line(im,(0,290),(640,290),(70,100,90),2)
        x=130+int(t*30);y=210
        cv2.circle(im,(x,y-35),16,(150,190,170),-1)
        cv2.line(im,(x,y-20),(x,y+35),(150,190,170),9)
        cv2.line(im,(x,y+35),(x-18,y+70),(150,190,170),7)
        cv2.line(im,(x,y+35),(x+18,y+70),(150,190,170),7)
        cv2.putText(im,f'{t:.2f}s',(540,330),cv2.FONT_HERSHEY_SIMPLEX,.5,(220,220,220),1)
        frames.append(Frame(t,n,encode(im)))
    write_clip(frames,path,'SIMULATED ENGINEERING FIXTURE');print(path)

if __name__=='__main__':main()
