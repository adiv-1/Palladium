import {useEffect,useRef,useState} from 'react';
import {FileVideo, Play} from 'lucide-react';
import manifest from '../../../demo/videos/manifest.json';
import quiet from '../../../demo/videos/quiet-scene.mp4?url';
import contact from '../../../demo/videos/staged-contact-and-fall.mp4?url';
import taking from '../../../demo/videos/staged-jacket-taking.mp4?url';
const urls=[quiet,contact,taking];
export default function DemoVideos({onAnalyze,busy}:{onAnalyze:(file:File)=>Promise<void>;busy:boolean}) {
  const [selected,setSelected]=useState(0),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const video=useRef<HTMLVideoElement>(null);
  useEffect(()=>{if(busy)video.current?.pause()},[busy]);
  const clip=manifest.files[selected];
  async function analyze() {
    setLoading(true);setError('');
    try {
      const response=await fetch(urls[selected]);if(!response.ok)throw Error('The original demo recording could not be loaded.');
      const bytes=await response.arrayBuffer();
      const digest=await crypto.subtle.digest('SHA-256',bytes);
      const actual=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
      if(actual!==clip.sha256)throw Error('The demo recording differs from its reviewed original.');
      await onAnalyze(new File([bytes],clip.original_filename,{type:'video/mp4'}));
    } catch(e) {setError((e as Error).message);} finally {setLoading(false);}
  }
  return <section className="demo-videos panel" aria-label="Original demonstration videos"><div className="section-heading"><div><span className="eyebrow">Demo recordings</span><h2>Three moments. Their original context.</h2></div><span className="pill">Original uploads</span></div><div className="demo-video-layout"><div className="demo-clip-list">{manifest.files.map((item,i)=><button key={item.file} aria-pressed={selected===i} className={selected===i?'selected':''} onClick={()=>{setSelected(i);setError('');}}><FileVideo size={17}/><span>{['Quiet scene','Staged contact & fall','Staged jacket taking'][i]}<small>{item.last_frame_seconds.toFixed(1)} seconds · {i===0?'ordinary activity':'staged reenactment'}</small></span><Play size={13}/></button>)}</div><video ref={video} key={clip.file} src={urls[selected]} controls playsInline preload="metadata" aria-label={`Preview ${clip.reviewed_title}`}/><div className="demo-clip-action"><h3>{clip.reviewed_title}</h3><p>{clip.reviewed_description}</p><button className="primary" disabled={busy||loading} onClick={()=>void analyze()}><Play size={14}/>{loading?'Loading original…':'Analyze this recording'}</button><p className="fine">Reviewed descriptions identify the recordings. Analysis runs the selected backend models; results appear in the live feed. Phone pairing remains available above.</p>{busy&&<p className="fine">Wait for the current source to finish before analyzing another recording.</p>}{error&&<p className="error" role="alert">{error}</p>}</div></div></section>;
}
