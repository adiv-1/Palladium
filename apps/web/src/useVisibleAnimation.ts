import {useEffect,useRef,useState} from 'react';
// Mounted animations pause when hidden; reduced motion shows the endpoint.
export function useVisibleAnimation(active:boolean,duration:number,onEnd?:()=>void) {
  const [progress,setProgress]=useState(0),done=useRef(onEnd);
  done.current=onEnd;
  useEffect(()=>{
    setProgress(0);if(!active)return;
    let frame=0,elapsed=0,last=0,disposed=false;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    function tick(time:number) {
      if(disposed||document.hidden)return;
      if(reduced.matches){setProgress(1);done.current?.();return;}
      if(last)elapsed+=Math.min(100,time-last);last=time;
      const next=Math.min(1,elapsed/duration);setProgress(next);
      if(next<1)frame=requestAnimationFrame(tick);else done.current?.();
    }
    const visibility=()=>{cancelAnimationFrame(frame);last=0;if(!document.hidden)frame=requestAnimationFrame(tick);};
    frame=requestAnimationFrame(tick);document.addEventListener('visibilitychange',visibility);
    return()=>{disposed=true;cancelAnimationFrame(frame);document.removeEventListener('visibilitychange',visibility);};
  },[active,duration]);
  return progress;
}
