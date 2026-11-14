import React,{useState,useEffect,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity,ArrowUpRight,Bell,Camera,Check,ChevronLeft,Clock,Eye,FileVideo,Layers,Lock,Radio,Shield,Smartphone,Upload,X,Sun,AlertTriangle,MapPin} from 'lucide-react';
import {QRCodeSVG} from 'qrcode.react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import './style.css';
const ActivityMap=React.lazy(()=>import('./ActivityMap'));

type AnyRecord=Record<string,any>;
async function api(path:string,options:RequestInit={}){
  const r=await fetch('/api'+path,options);
  if(!r.ok){let detail='Request failed';try{detail=(await r.json()).detail}catch{}throw Object.assign(Error(typeof detail==='string'?detail:JSON.stringify(detail)),{status:r.status})}return r.json();
}
const post=(path:string,body:unknown)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const wsUrl=(path:string)=>`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${path}`;
const media=(id:string,kind:string)=>`/api/incidents/${id}/media/${kind}`;

function Capture(){
  const video=useRef<HTMLVideoElement>(null),socket=useRef<WebSocket|null>(null),stream=useRef<MediaStream|null>(null),timer=useRef<number>(0),running=useRef(false),wake=useRef<any>(null);
  const [status,setStatus]=useState('Ready to connect'),[frames,setFrames]=useState(0),[error,setError]=useState(''),[active,setActive]=useState(false),[facing,setFacing]=useState('environment');
  const stop=()=>{running.current=false;clearTimeout(timer.current);socket.current?.close();stream.current?.getTracks().forEach(t=>t.stop());wake.current?.release();setActive(false)};
  useEffect(()=>()=>stop(),[]);
  async function start(){
    stop();setError('');setStatus('Opening camera…');
    try{
      if(!window.isSecureContext)throw Error('Camera access needs HTTPS. Open the secure pairing URL from the dashboard.');
      if(!navigator.mediaDevices?.getUserMedia)throw Error('This browser does not support camera capture. Use Safari or Chrome.');
      const token=location.hash.slice(1);if(!token)throw Error('Open the pairing link from the SafeSC dashboard first.');
      stream.current=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:facing},width:{ideal:1280},height:{ideal:720},frameRate:{ideal:15}},audio:false});
      video.current!.srcObject=stream.current;await video.current!.play();
      const ws=new WebSocket(wsUrl('/ws/capture'));socket.current=ws;running.current=true;setActive(true);
      const canvas=document.createElement('canvas');let fps=8;let sending=false;let sentAt=0;
      const send=()=>{
        if(!running.current||ws.readyState!==WebSocket.OPEN||sending)return;
        const v=video.current!;if(!v.videoWidth){timer.current=window.setTimeout(send,100);return}
        const ratio=Math.min(1,1280/v.videoWidth);canvas.width=Math.floor(v.videoWidth*ratio/2)*2;canvas.height=Math.floor(v.videoHeight*ratio/2)*2;
        canvas.getContext('2d')!.drawImage(v,0,0,canvas.width,canvas.height);sending=true;
        sentAt=performance.now();const timestamp=sentAt/1000;
        canvas.toBlob(async blob=>{if(!blob||ws.readyState!==WebSocket.OPEN){sending=false;return}
          ws.send(JSON.stringify({type:'frame',timestamp}));ws.send(await blob.arrayBuffer());},'image/jpeg',.82);
      };
      ws.onopen=()=>{setStatus('Pairing with SafeSC…');ws.send(JSON.stringify({token}))};
      ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='ready'){fps=m.fps;setStatus('Connected · streaming');send()}
        if(m.type==='ack'){sending=false;setFrames(m.index);setStatus(m.model_status==='error'?'Streaming · model unavailable':m.model_status==='waiting_for_gpu'?'Streaming · analysis queued':m.model_status==='loading'?'Streaming · analysis starting':'Connected · streaming');timer.current=window.setTimeout(send,Math.max(0,1000/fps-(performance.now()-sentAt)))}
        if(m.type==='error'){setError(m.message);stop()}};
      ws.onclose=e=>{if(running.current){setStatus('Disconnected');setError(e.reason||'Connection closed. Tap Start camera to reconnect.');stop()}};
      ws.onerror=()=>setError('Unable to reach server. Check your connection and secure pairing URL.');
      try{wake.current=await (navigator as any).wakeLock?.request('screen')}catch{}
    }catch(e){setError(String(e));stop();setStatus('Not connected')}
  }
  return <div className="capture-page"><div className="brand"><Shield size={26}/><strong>Safe<span>SC</span></strong><small>CAMERA LINK</small></div><h1>Your phone.<br/>A clearer view.</h1><p>Secure camera link to your SafeSC review workspace. Audio is off.</p>
    <div className="phone-preview"><video ref={video} autoPlay playsInline muted/>{!active&&<Camera size={48}/>}<span className="phone-status"><i className={active?'dot green':'dot'}/>{status}</span></div>
    <div className="capture-stats"><span>{frames} frames acknowledged</span><span>Rear camera · JPEG stream</span></div>
    {error&&<div className="error" role="alert">{error}</div>}
    {!active?<><select aria-label="Camera" value={facing} onChange={e=>setFacing(e.target.value)}><option value="environment">Rear camera</option><option value="user">Front camera</option></select><button className="primary wide" onClick={start}><Camera size={18}/>Start camera</button></>:<button className="danger wide" onClick={stop}>Stop streaming</button>}
    <p className="fine">Keep this page open and the phone awake. Landscape gives the clearest event view. Works over Wi-Fi or cellular. If the connection drops, reconnect here; a new stream starts at zero.</p></div>
}

function Trajectories({url}:{url:string}){
  const host=useRef<HTMLDivElement>(null),index=useRef(0);const [data,setData]=useState<AnyRecord|null>(null),[frame,setFrame]=useState(0),[error,setError]=useState('');
  useEffect(()=>{fetch(url).then(r=>r.json()).then(setData).catch(e=>setError(String(e)))},[url]);
  useEffect(()=>{if(!data||!host.current)return;const el=host.current;
    const scene=new THREE.Scene();scene.background=new THREE.Color('#101b22');const camera=new THREE.PerspectiveCamera(45,el.clientWidth/320,.01,10000);camera.position.set(2,2,4);
    const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setSize(el.clientWidth,320);el.appendChild(renderer.domElement);
    const controls=new OrbitControls(camera,renderer.domElement);scene.add(new THREE.GridHelper(10,20,0x34515c,0x20343e));
    const geometry=new THREE.BufferGeometry();const points=new THREE.Points(geometry,new THREE.PointsMaterial({color:0x63ddbf,size:.035}));scene.add(points);
    const box=new THREE.Box3().setFromPoints(data.points[0].map((p:number[])=>new THREE.Vector3(...p as [number,number,number])));
    const center=box.getCenter(new THREE.Vector3());controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(1,1,2).multiplyScalar(Math.max(box.getSize(new THREE.Vector3()).length(),1)));
    let raf=0;function render(){geometry.setAttribute('position',new THREE.Float32BufferAttribute(data!.points[index.current].flat(),3));controls.update();renderer.render(scene,camera);raf=requestAnimationFrame(render)}render();
    return()=>{cancelAnimationFrame(raf);controls.dispose();geometry.dispose();(points.material as THREE.Material).dispose();renderer.dispose();el.replaceChildren()};
  },[data]);
  return <section className="panel"><div className="panel-title"><Layers size={18}/><h3>Estimated 3D reconstruction</h3></div><p className="fine">{data?.simulated?'SIMULATED FIXTURE':data?.units||'Loading trajectories…'}</p>{error&&<p>{error}</p>}<div ref={host}/>{data&&<input aria-label="Trajectory frame" type="range" min={0} max={data.points.length-1} value={frame} onChange={e=>{index.current=+e.target.value;setFrame(+e.target.value)}}/>}<small>Frame {frame} · Drag to orbit</small></section>
}

function ReviewPage({id,onBack}:{id:string,onBack:()=>void}){
  const [detail,setDetail]=useState<AnyRecord|null>(null),[error,setError]=useState(''),[note,setNote]=useState('');const videos=useRef<(HTMLVideoElement|null)[]>([]);
  const load=()=>api('/incidents/'+id).then(setDetail).catch(e=>setError(e.message));useEffect(()=>{load();const t=setInterval(load,4000);return()=>clearInterval(t)},[id]);
  if(!detail)return <p>{error||'Loading incident…'}</p>;const i=detail.incident,r=i.record;
  async function decide(decision:string){try{await post(`/incidents/${id}/review`,{decision,note});load()}catch(e){setError(String(e))}}
  const available=['original','enhanced','review','bvimamba_review'].filter(k=>r.files[k]);
  return <><button className="text-button" onClick={onBack}><ChevronLeft size={18}/>Back to incident queue</button><div className="page-heading"><div><span className="eyebrow">INCIDENT REVIEW</span><h1>Every detail. In context.</h1><p>{id} · {new Date(i.created_at).toLocaleString()}</p></div><span className="pill amber">{i.review_status.replace('_',' ')}</span></div>
    {i.simulated&&<div className="simulation">SIMULATED DEVELOPMENT TRIGGER · Derived model artifacts, if present, have separate provenance.</div>}{r.trigger_origin==='reviewer'&&<div className="simulation">REVIEWER-MARKED MOMENT · This was saved by a person, not asserted as an AI incident.</div>}{error&&<div className="error">{error}</div>}
    <div className="review-videos">{available.map((k,n)=><section className="panel" key={k}><div className="panel-title"><FileVideo size={18}/><h3>{k==='original'?'Original incident window':k==='enhanced'?'Low-light enhanced':k==='bvimamba_review'?'BVI comparison copy':'AI-enhanced review copy'}</h3></div><video controls playsInline src={media(id,k)} ref={el=>{videos.current[n]=el}} onSeeked={e=>{const t=e.currentTarget.currentTime;videos.current.forEach(v=>{if(v&&Math.abs(v.currentTime-t)>.4)v.currentTime=t})}}/><p className="fine">{k==='original'?'Extracted from the source; stream timestamps preserved.':'Derived visualization. Generated details are estimates.'}</p></section>)}</div>
    <div className="review-grid"><section className="panel"><div className="panel-title"><Eye size={18}/><h3>Observable actions</h3></div>{r.observable_events.map((e:AnyRecord,n:number)=><article className="observation" key={n}><time>{e.timestamp_start.toFixed(1)} – {e.timestamp_end.toFixed(1)}s</time><p>{e.observation}</p><small>{e.actors.join(' · ')}</small></article>)}<h4>Uncertainty</h4>{r.uncertainties.map((u:string)=><p className="uncertainty" key={u}>{u}</p>)}<p className="fine">{r.trigger_origin!=='reviewer'&&<>Score {(i.confidence*100).toFixed(0)}% · </>}{r.confidence_note}</p><h4>Supporting frames</h4><div className="frame-links">{r.supporting_frames.map((f:number)=>{const stamp=r.frame_timestamps.find((x:AnyRecord)=>x.index===f)?.timestamp;return <button key={f} onClick={()=>videos.current.forEach(v=>{if(v&&stamp!==undefined)v.currentTime=stamp-r.clip_offset})}>#{f}{stamp!==undefined?` · ${stamp.toFixed(1)}s`:''}</button>})}</div></section>
    <section className="panel"><div className="panel-title"><Check size={18}/><h3>Human review</h3></div><p>Choose the next step. Decisions remain inside this workspace.</p><textarea aria-label="Reviewer note" placeholder="Add context or a reviewer note…" value={note} onChange={e=>setNote(e.target.value)}/><div className="review-actions"><button className="primary" onClick={()=>decide('confirmed')}>Confirm for follow-up</button><button onClick={()=>decide('needs_review')}>Needs more review</button><button onClick={()=>decide('dismissed')}>Dismiss</button></div><h4>Review history</h4>{detail.history.map((h:AnyRecord)=><p className="fine" key={h.id}>{new Date(h.timestamp).toLocaleTimeString()} · {h.decision} {h.note&&`— ${h.note}`}</p>)}</section></div>
    {r.files.trajectory_original&&<Trajectories url={media(id,'trajectory_original')}/>}
    <section className="panel"><div className="panel-title"><Lock size={18}/><h3>Provenance & processing</h3></div><p className="hash">Original window SHA-256: {r.original_sha256}</p><p className="fine">Streaming observer: {r.model.name} · Revision: {r.model.revision} · Lighting: {r.lighting_condition}</p>{r.observation_model&&<p className="fine">Observation verifier: {r.observation_model.name} · Revision: {r.observation_model.revision}</p>}{detail.jobs.map((j:AnyRecord)=><div className="job" key={j.id}><span>{j.kind} · {j.status}</span>{j.error&&<p className="error">{j.error}</p>}{j.status==='failed'&&<button onClick={()=>post(`/jobs/${j.id}/retry`,{}).then(load)}>Retry job</button>}</div>)}<p className="fine">Queued review jobs start after live monitoring stops. Phone intake remains available during a running job.</p><button onClick={()=>post(`/incidents/${id}/compare`,{}).then(load).catch(e=>setError(String(e)))}>Generate another enhancement comparison</button><details><summary>Transformation log</summary><pre>{JSON.stringify(r.transforms,null,2)}</pre></details><details><summary>Model response audit</summary><pre>{JSON.stringify(r.model_output_audit||[],null,2)}</pre></details></section>
  </>
}

function App(){
  const [bookmarkNotice,setBookmarkNotice]=useState('');
  const seenIncidents=useRef(new Set<string>());
  const [reviewAlert,setReviewAlert]=useState<AnyRecord|null>(null),[notificationPermission,setNotificationPermission]=useState('Notification' in window?Notification.permission:'unavailable');
  const [authed,setAuthed]=useState(false),[token,setToken]=useState(''),[sources,setSources]=useState<AnyRecord[]>([]),[incidents,setIncidents]=useState<AnyRecord[]>([]),[health,setHealth]=useState<AnyRecord>({}),[selected,setSelected]=useState<string|null>(null),[pair,setPair]=useState(''),[error,setError]=useState(''),[uploading,setUploading]=useState(false),[tick,setTick]=useState(0),[connected,setConnected]=useState(false),[tab,setTab]=useState('Overview');
  const refresh=async()=>{try{const [s,i,h]=await Promise.all([api('/sources'),api('/incidents'),api('/health')]);i.forEach((incident:AnyRecord)=>seenIncidents.current.add(incident.id));setSources(s);setIncidents(i);setHealth(h);setAuthed(true)}catch(e:any){if(e.status===401)setAuthed(false);else setConnected(false)}};
  useEffect(()=>{refresh()},[]);
  useEffect(()=>{if(!authed)return;let ws:WebSocket;let retry:number;let stopped=false;
    const connect=()=>{ws=new WebSocket(wsUrl('/ws/events'));ws.onopen=()=>setConnected(true);ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='source')setSources(old=>[m.payload,...old.filter(s=>s.id!==m.payload.id)]);if(m.type==='incident'){
      if(!seenIncidents.current.has(m.payload.id)){
        seenIncidents.current.add(m.payload.id);setReviewAlert(m.payload);
        if('Notification' in window&&Notification.permission==='granted'&&document.hidden){
          const notice=new Notification(m.payload.record?.trigger_origin==='reviewer'?'SafeSC · Marked moment saved':'SafeSC · Review requested',{body:'A possible safety event has supporting footage. Open the original clip and assess the context.',tag:m.payload.id});
          notice.onclick=()=>{window.focus();setSelected(m.payload.id);setReviewAlert(null);notice.close()};
        }
      }
      setIncidents(old=>[m.payload,...old.filter(i=>i.id!==m.payload.id)])
    }};ws.onclose=()=>{setConnected(false);if(!stopped)retry=window.setTimeout(connect,2000)}};connect();
    const poll=setInterval(()=>{refresh();setTick(t=>t+1)},2500);return()=>{stopped=true;clearInterval(poll);clearTimeout(retry);ws?.close()};
  },[authed]);
  async function pairPhone(){try{setError('');const r=await post('/sources',{name:'Mobile camera',kind:'phone'});setPair(r.capture_url);refresh()}catch(e){setError(String(e))}}
  async function upload(file:File){setUploading(true);setError('');try{const body=new FormData();body.append('file',file);await api('/upload',{method:'POST',body});refresh()}catch(e){setError(String(e))}finally{setUploading(false)}}
  async function markMoment(sourceId:string){try{await post(`/sources/${sourceId}/bookmark`,{});setBookmarkNotice('Moment marked. Surrounding footage will appear for review after its context is saved.')}catch(e){setError(String(e))}}
  async function exportFeedback(){try{const data=await api('/reviews/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='safesc-review-history.json';link.click();URL.revokeObjectURL(url)}catch(e){setError(String(e))}}
  if(!authed)return <div className="login"><div className="brand"><Shield/><strong>Safe<span>SC</span></strong></div><span className="eyebrow">AUTHORIZED REVIEW WORKSPACE</span><h1>A clearer view.<br/>A human decision.</h1><p>Enter your workspace access token to continue.</p><form onSubmit={async e=>{e.preventDefault();try{await post('/login',{token});refresh()}catch(e){setError(String(e))}}}><input type="password" autoComplete="current-password" aria-label="Workspace access token" placeholder="Workspace access token" value={token} onChange={e=>setToken(e.target.value)}/><button className="primary">Open workspace <ArrowUpRight size={18}/></button></form>{error&&<p className="error">{error}</p>}<small>Access token: data/app_token on the server</small></div>;
  const source=sources.find(s=>s.status==='streaming')||sources[0],pending=incidents.filter(i=>i.review_status==='pending').length;
  return <div className="shell"><aside><div className="brand"><Shield size={27}/><strong>Safe<span>SC</span></strong></div><div className="workspace"><i className="dot green"/><div>Campus safety lab<small>HUMAN REVIEW WORKSPACE</small></div></div><span className="nav-label">WORKSPACE</span>{[['Overview',Activity],['Live monitor',Radio],['Incident queue',Layers],['Campus activity',MapPin],['Privacy & controls',Lock]].map(([label,Icon]:any)=><button key={label} className={'nav '+(tab===label?'active':'')} onClick={()=>{setTab(label);setSelected(null)}}><Icon size={18}/>{label}{label==='Incident queue'&&pending>0&&<b>{pending}</b>}</button>)}<div className="sidebar-bottom"><Shield size={20}/><p>AI observes.<br/><strong>People decide.</strong></p><small>No identity recognition.<br/>No automatic dispatch.</small></div></aside>
    <div className="main"><header><span>Workspace <span className="slash">/</span> {selected?'Incident review':tab}</span><div>{notificationPermission!=='unavailable'&&<button className="text-button" title="Notify this reviewer while the workspace is open. No campus-wide alerts are sent." onClick={async()=>{setNotificationPermission(await Notification.requestPermission())}}><Bell size={16}/>{notificationPermission==='granted'?'Review alerts on':'Enable review alerts'}</button>}<i className={'dot '+(connected?'green':'')}/>{connected?'Workspace connected':'Reconnecting'}<span className="avatar">SC</span></div></header><main>
    {reviewAlert&&<div className="review-notification" role="status"><AlertTriangle size={20}/><div><strong>{reviewAlert.record?.trigger_origin==='reviewer'?'Marked moment saved · review required':'Possible incident · review required'}</strong><p>Open the original footage, assess the uncertainty, then confirm or dismiss.</p></div><button onClick={()=>{setSelected(reviewAlert.id);setReviewAlert(null)}}>Review footage <ArrowUpRight size={16}/></button><button className="text-button" aria-label="Dismiss notification" onClick={()=>setReviewAlert(null)}><X size={16}/></button></div>}
    {selected?<ReviewPage id={selected} onBack={()=>setSelected(null)}/>:tab==='Campus activity'?<React.Suspense fallback={<p>Loading campus activity…</p>}><ActivityMap/></React.Suspense>:<><div className="page-heading"><div><span className="eyebrow">SAFETY, WITH CONTEXT</span><h1>{tab==='Privacy & controls'?'Trust is part of the system.':tab==='Incident queue'?'The moments worth a closer look.':'A clearer view of campus.'}</h1><p>Observable events. Preserved context. Human decisions.</p></div><button onClick={pairPhone}><Smartphone size={17}/>Connect phone</button></div>
    {error&&<div className="error" role="alert">{error}</div>}{health.simulated&&<div className="simulation">SIMULATED DEVELOPMENT MODE · Fixture alerts are not real model results.</div>}
    {source?.status==='streaming'&&<div className="live-actions"><button onClick={()=>markMoment(source.id)}><Eye size={16}/>Mark moment for review</button><span className="fine">Save context when you see something that deserves a closer look.</span></div>}
    {bookmarkNotice&&<p className="fine" role="status">{bookmarkNotice}</p>}
    {tab==='Privacy & controls'&&<p><button onClick={exportFeedback}>Export review history</button><span className="fine"> Reviewer feedback is auditable. Model training requires a separate evaluated release.</span></p>}
    {tab==='Privacy & controls'?<div className="controls-grid">{[['Originals preserved','Untouched uploads and hashed incident windows are stored separately from generated review copies.'],['People stay anonymous','Only temporary person labels are used. Identity and demographic inference are excluded.'],['Human review required','Confirm, dismiss or request more review. No external dispatch occurs.'],['Short retention',`Incident media expires after ${health.retention_hours||24} hours. Reviewer decisions remain auditable.`],['Honest processing','Unavailable models report errors. Fixtures and enhanced videos carry visible labels.'],['Secure phone capture','Pairing links expire in one hour. Camera capture requires HTTPS; audio stays off.']].map(([title,copy])=><section className="panel" key={title}><Lock size={20}/><h3>{title}</h3><p>{copy}</p></section>)}</div>:<>
    <div className="stats"><div><span>Connected sources</span><strong>{sources.filter(s=>s.status==='streaming').length}<small> / 1</small></strong><p><i className="dot green"/>Shared GPU pipeline</p></div><div><span>Awaiting review</span><strong>{pending.toString().padStart(2,'0')}</strong><p>Human attention required</p></div><div><span>Inference engine</span><strong className="model-name">{health.streaming_model==='moss'?'MOSS-VL':health.streaming_model||'—'}</strong><p>{health.gpu?.[0]||'GPU not connected'}</p></div><div><span>Evidence integrity</span><strong className="model-name">SHA-256</strong><p><Lock size={12}/>Original windows hashed</p></div></div>
    {tab!=='Incident queue'&&<section className="monitor panel"><div className="panel-title"><Radio size={18}/><h3>Live monitor</h3><span className={'pill '+(source?.status==='streaming'?'teal':'')}>{source?.status||'No source connected'}</span></div><div className="monitor-grid"><div className="video-stage">{source?.status==='streaming'?<img src={`/api/sources/${source.id}/frame?t=${tick}`} alt="Current original camera frame"/>:<div className="empty-camera"><div className="camera-ring"><Camera size={34}/></div><h3>A view starts here.</h3><p>Pair your phone or upload a video to begin.</p><div><button className="primary" onClick={pairPhone}><Smartphone size={16}/>Pair phone camera</button><label className="button"><Upload size={16}/>Upload video<input hidden type="file" accept="video/mp4,video/quicktime,video/webm" onChange={e=>e.target.files?.[0]&&upload(e.target.files[0])}/></label></div></div>}<div className="video-caption"><span><i className="dot"/>ORIGINAL FEED</span><span>{source?.name||'Camera awaiting connection'}</span></div></div><div className="telemetry"><span className="eyebrow">STREAM TELEMETRY</span><h3>{source?.name||'No active camera'}</h3>{[[Sun,'Lighting',source?.telemetry?.lighting?.state?.replaceAll('_',' ')||'Waiting'],[Clock,'Pre-event buffer',`${(source?.telemetry?.buffer_seconds||0).toFixed(1)}s / 10s`],[Activity,'Observed activity',source?.telemetry?.safety_state==='NORMAL'?'No event flagged in assessed frames':source?.telemetry?.safety_state?.replaceAll('_',' ')||'Awaiting video'],[Eye,'Model status',source?.telemetry?.model_status||'Not started']].map(([Icon,label,value]:any)=><div className="telemetry-row" key={label}><span><Icon size={15}/>{label}</span><strong>{value}</strong></div>)}<div className="observation-box"><span className="eyebrow">LATEST OBSERVATION</span><p>{source?.telemetry?.last_observation?.observable_events?.[0]?.observation||'Observations appear when sufficient evidence is available.'}</p></div>{source?.telemetry?.error&&<p className="error">{source.telemetry.error}</p>}</div></div></section>}
    <section className="queue"><div className="section-heading"><div><h2>Incident queue <span>{incidents.length}</span></h2><p>Possible safety events, ready for a human perspective.</p></div><span className="fine">{uploading?'Uploading video…':'Newest first'}</span></div>{!incidents.length?<div className="empty-queue"><Check size={26}/><h3>No incidents to review.</h3><p>Flagged moments will appear here with supporting context.</p></div>:<div className="incident-grid">{incidents.map(i=><button className="incident-card" key={i.id} onClick={()=>setSelected(i.id)}><div className="card-image"><img src={media(i.id,'thumbnail')} alt="Incident thumbnail"/><span className="pill amber">{i.simulated?'SIMULATED':i.severity.replaceAll('_',' ')}</span></div><div className="card-copy"><small><Clock size={12}/>{new Date(i.created_at).toLocaleTimeString()}<span>{i.review_status.replace('_',' ')}</span></small><h3>{i.record.observable_events[0]?.observation||'Possible safety incident'}</h3><p>{(i.end_time-i.start_time).toFixed(1)}s preserved · {i.record.lighting_condition.replaceAll('_',' ')}</p><div>Review evidence <ArrowUpRight size={17}/></div></div></button>)}</div>}</section></>}
    </>}
    <footer><Shield size={14}/>SafeSC is a human decision-support prototype.<span>Built for Trojans, by Trojans.</span></footer></main></div>
    {pair&&<div className="modal-backdrop" onClick={()=>setPair('')}><section className="pair-modal" onClick={e=>e.stopPropagation()}><button className="close" onClick={()=>setPair('')} aria-label="Close pairing"><X/></button><Smartphone size={30}/><h2>Bring your phone into view.</h2><p>Scan with your phone camera. Open the link, allow camera access, then tap Start camera.</p><div className="qr"><QRCodeSVG value={pair} size={210}/></div><input readOnly aria-label="Phone pairing URL" value={pair}/><button className="primary wide" onClick={()=>navigator.clipboard.writeText(pair)}>Copy pairing link</button><p className="fine">Link expires in 1 hour. HTTPS is required on your phone. Keep the capture page open.</p></section></div>}</div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode>{location.pathname==='/capture'?<Capture/>:<App/>}</React.StrictMode>);
