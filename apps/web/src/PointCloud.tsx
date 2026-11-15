import {useEffect,useRef,useState} from 'react';
import {Layers,RotateCcw} from 'lucide-react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
type Data=Record<string,any>;
const seconds=(n:number)=>{const t=Math.round(Math.max(0,n)*10);return `${Math.floor(t/600)}:${String(Math.floor(t/10)%60).padStart(2,'0')}.${t%10}`};
export default function PointCloud({url,playhead,offset,onSeek}:{url:string,playhead:number,offset:number,onSeek:(t:number)=>void}){
  const host=useRef<HTMLDivElement>(null),frameRef=useRef(0),reset=useRef<()=>void>(()=>{});
  const [data,setData]=useState<Data|null>(null),[open,setOpen]=useState(true),[error,setError]=useState('');
  useEffect(()=>{let active=true;setData(null);setError('');fetch(url).then(r=>{if(!r.ok)throw Error('Reconstruction unavailable');return r.json()}).then(d=>{if(!Array.isArray(d.points)||!d.points.length)throw Error('No valid 3D points were exported');if(active)setData(d)}).catch(e=>active&&setError(String(e)));return()=>{active=false}},[url]);
  const sourceTimes:number[]=data?.source_frames?.map((f:Data)=>Math.max(0,f.timestamp-offset))||[];
  let frame=0;sourceTimes.forEach((t,n)=>{if(t<=playhead+.015)frame=n});frameRef.current=frame;
  useEffect(()=>{
    if(!open||!data||!host.current)return;const el=host.current;
    let renderer:THREE.WebGLRenderer;
    try{renderer=new THREE.WebGLRenderer({antialias:true})}catch{setError('3D rendering is unavailable in this browser. Use the motion-tracks video above.');return}
    renderer.setPixelRatio(Math.min(devicePixelRatio,2));el.appendChild(renderer.domElement);
    const scene=new THREE.Scene();scene.background=new THREE.Color('#101d24');
    const h=data.image_size?.[1]||504,fy=data.intrinsics?.[0]?.[1]?.[1]||h;
    const camera=new THREE.PerspectiveCamera(THREE.MathUtils.radToDeg(2*Math.atan(h/(2*fy))),1,.01,1000);
    const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=!matchMedia('(prefers-reduced-motion: reduce)').matches;
    const depths=data.points[0].map((p:number[])=>p[2]).filter((z:number)=>Number.isFinite(z)&&z>0).sort((a:number,b:number)=>a-b);
    const depth=depths[Math.floor(depths.length*.5)]||5;
    reset.current=()=>{camera.position.set(0,0,0);camera.up.set(0,1,0);controls.target.set(0,0,-depth);camera.lookAt(controls.target);controls.update()};reset.current();
    const geometry=new THREE.BufferGeometry(),count=data.points[0].length;
    const positions=new Float32Array(count*3),colors=new Float32Array(count*3);
    geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));
    const material=new THREE.PointsMaterial({vertexColors:true,size:2.6,sizeAttenuation:false});const points=new THREE.Points(geometry,material);points.frustumCulled=false;scene.add(points);
    const resize=()=>{const width=Math.max(1,el.clientWidth);renderer.setSize(width,360);camera.aspect=width/360;camera.updateProjectionMatrix()};
    const observer=new ResizeObserver(resize);observer.observe(el);resize();
    let raf=0,last=-1;
    const visible=()=>{cancelAnimationFrame(raf);if(!document.hidden)render()};
    document.addEventListener('visibilitychange',visible);
    function render(){const f=Math.min(frameRef.current,data!.points.length-1);
      if(f!==last){let n=0;data!.points[f].forEach((p:number[],i:number)=>{
        if(!p.every(Number.isFinite)||p[2]<=0||(data!.confidence?.[f]&&data!.confidence[f][i]<.5))return;
        positions[n*3]=p[0];positions[n*3+1]=-p[1];positions[n*3+2]=-p[2];
        const rgb=data!.colors?.[i]||[99,221,191],c=new THREE.Color(rgb[0]/255,rgb[1]/255,rgb[2]/255).convertSRGBToLinear();
        colors.set([c.r,c.g,c.b],n*3);n++;
      });geometry.setDrawRange(0,n);geometry.attributes.position.needsUpdate=true;geometry.attributes.color.needsUpdate=true;last=f}
      controls.update();renderer.render(scene,camera);if(!document.hidden)raf=requestAnimationFrame(render)
    }render();return()=>{cancelAnimationFrame(raf);document.removeEventListener('visibilitychange',visible);observer.disconnect();controls.dispose();geometry.dispose();material.dispose();renderer.dispose();el.replaceChildren()};
  },[data,open]);
  return <section className="panel geometry-panel"><div className="panel-title"><Layers size={18}/><h3>Explore the estimated 3D points</h3></div><p>Use the motion-tracks video first. This optional view lets you orbit the same scene points and inspect their estimated movement.</p><p className="fine">This is a colored point cloud, not a body mesh or a reconstruction of hidden actions. Scale is in geometry-model units, not measured meters. Only points observed by the tracker are displayed.</p>
    {error&&<p className="error">{error}</p>}
    <button onClick={()=>setOpen(!open)}>{open?'Close 3D explorer':'Open 3D explorer'}</button>
    {open&&<><div className="pointcloud-host" ref={host}/>{data&&<><div className="geometry-controls"><button onClick={()=>reset.current()}><RotateCcw size={14}/>Reset camera</button><span>{data.quality?.sampled_points||data.points[0].length} sampled points · {Math.round((data.quality?.observed_fraction?.[frame]??1)*100)}% observed in this frame</span></div><input aria-label="3D source time" type="range" min={0} max={data.points.length-1} value={frame} onChange={e=>onSeek(sourceTimes[+e.target.value]||0)}/><p className="fine">Clip {seconds(sourceTimes[frame]||0)} · source frame {data.source_frames?.[frame]?.index??frame} · drag to orbit, scroll to zoom</p></>}</>}
  </section>
}
