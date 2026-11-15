import React,{useState,useEffect,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity,ArrowUpRight,Bell,Camera,Check,ChevronLeft,Clock,Eye,FileVideo,Lock,Radio,Shield,Smartphone,Upload,X,Sun,AlertTriangle,MapPin,BookOpen} from 'lucide-react';
import {QRCodeSVG} from 'qrcode.react';
import './theme.css';
import './style.css';
import {initTheme} from './theme';
import {ThemeToggle} from './ThemeToggle';
import LiveMonitor,{incidentLabel} from './LiveMonitor';
import ReviewPage from './IncidentReview';
import StorageStatus from './StorageStatus';
import {Logo} from './Logo';
import LearningRepository,{LearningPermission} from './LearningRepository';
const ActivityMap=React.lazy(()=>import('./ActivityMap'));

initTheme();
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
  const [status,setStatus]=useState('Ready to connect'),[frames,setFrames]=useState(0),[error,setError]=useState(''),[active,setActive]=useState(false),[facing,setFacing]=useState('environment'),[quality,setQuality]=useState('fast');
  const stop=()=>{running.current=false;clearTimeout(timer.current);socket.current?.close();stream.current?.getTracks().forEach(t=>t.stop());wake.current?.release();setActive(false)};
  useEffect(()=>()=>stop(),[]);
  async function start(){
    stop();setError('');setStatus('Opening camera…');
    try{
      if(!window.isSecureContext)throw Error('Camera access needs HTTPS. Open the secure pairing URL from the dashboard.');
      if(!navigator.mediaDevices?.getUserMedia)throw Error('This browser does not support camera capture. Use Safari or Chrome.');
      const token=location.hash.slice(1);if(!token)throw Error('Open the pairing link from the Palladium dashboard first.');
      stream.current=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:facing},width:{ideal:1280},height:{ideal:720},frameRate:{ideal:15}},audio:false});
      video.current!.srcObject=stream.current;await video.current!.play();
      const ws=new WebSocket(wsUrl('/ws/capture'));socket.current=ws;running.current=true;setActive(true);
      const canvas=document.createElement('canvas');let fps=12,encoding=false,inFlight=0;
      const send=()=>{
        if(!running.current||ws!==socket.current||ws.readyState!==WebSocket.OPEN)return;
        timer.current=window.setTimeout(send,1000/fps);
        if(encoding||inFlight>=2||ws.bufferedAmount>512*1024)return;
        const v=video.current!;if(!v.videoWidth)return;
        const ratio=Math.min(1,(quality==='fast'?960:1280)/Math.max(v.videoWidth,v.videoHeight));canvas.width=Math.floor(v.videoWidth*ratio/2)*2;canvas.height=Math.floor(v.videoHeight*ratio/2)*2;
        canvas.getContext('2d')!.drawImage(v,0,0,canvas.width,canvas.height);encoding=true;
        const timestamp=performance.now()/1000;
        canvas.toBlob(async blob=>{try{if(!blob||!running.current||ws!==socket.current||ws.readyState!==WebSocket.OPEN)return;
          const bytes=await blob.arrayBuffer();if(ws.readyState!==WebSocket.OPEN)return;
          inFlight++;ws.send(JSON.stringify({type:'frame',timestamp}));ws.send(bytes);
        }finally{encoding=false}},'image/jpeg',quality==='fast'?.72:.85);
      };
      ws.onopen=()=>{setStatus('Pairing with Palladium…');ws.send(JSON.stringify({token}))};
      ws.onmessage=e=>{if(ws!==socket.current)return;const m=JSON.parse(e.data);if(m.type==='ready'){fps=Math.min(m.fps,quality==='fast'?12:8);setStatus('Connected · streaming');send()}
        if(m.type==='ack'){inFlight=Math.max(0,inFlight-1);setFrames(m.index);setStatus(m.model_status==='error'?'Streaming · model unavailable':m.model_status==='waiting_for_gpu'?'Streaming · analysis queued':m.model_status==='loading'?'Streaming · analysis starting':'Connected · streaming')}
        if(m.type==='error'){setError(m.message);stop()}};
      ws.onclose=e=>{if(ws===socket.current&&running.current){setStatus('Disconnected');setError(e.reason||'Connection closed. Tap Start camera to reconnect.');stop()}};
      ws.onerror=()=>setError('Unable to reach server. Check your connection and secure pairing URL.');
      try{wake.current=await (navigator as any).wakeLock?.request('screen')}catch{}
    }catch(e){setError(String(e));stop();setStatus('Not connected')}
  }
  return <div className="capture-page"><div className="brand-lockup"><Logo variant="mark" size={28}/><span className="wordmark">PALLADIUM</span><small className="lockup-tag">CAMERA LINK</small></div><h1>Your phone.<br/>A clearer view.</h1><p>Secure camera link to your Palladium review workspace. Audio is off.</p>
    <div className="phone-preview"><video ref={video} autoPlay playsInline muted/>{!active&&<Camera size={48}/>}<span className="phone-status"><i className={active?'dot green':'dot'}/>{status}</span></div>
    <div className="capture-stats"><span>{frames} frames acknowledged</span><span>{facing==='environment'?'Rear':'Front'} camera · {quality==='fast'?'Low latency':'More detail'}</span></div>
    {error&&<div className="error" role="alert">{error}</div>}
    {!active?<><select aria-label="Camera" value={facing} onChange={e=>setFacing(e.target.value)}><option value="environment">Rear camera</option><option value="user">Front camera</option></select><select aria-label="Stream quality" value={quality} onChange={e=>setQuality(e.target.value)}><option value="fast">Low latency · 960px · up to 12 FPS</option><option value="detail">More detail · 1280px · up to 8 FPS</option></select><button className="primary wide" onClick={start}><Camera size={18}/>Start camera</button></>:<button className="danger wide" onClick={stop}>Stop streaming</button>}
    <p className="fine">Keep this page open and the phone awake. Landscape gives the clearest event view. Works over Wi-Fi or cellular. If the connection drops, reconnect here; a new stream starts at zero.</p></div>
}


const hasCommandCenter = import.meta.env.MODE === 'hosted';
// Five sidebar sections, each with one job. A section may hold sub-views (segmented tabs). Sub-views with a
// `src` embed the 3D command view in a same-origin frame (so the theme choice is shared); they are hosted-only.
type SubView = {id: string, hosted?: boolean, src?: string};
type Section = {id: string, icon: any, title: string, desc: string, note?: string, subs: SubView[]};
const sections: Section[] = [
  {id: 'Home', icon: Activity, title: 'Palladium', desc: 'Campus safety where AI watches and people decide.',
    subs: [{id: 'Status'}, {id: 'How it works', hosted: true, src: '/command/?embedded=1#/about'}]},
  {id: 'Respond', icon: Radio, title: 'Review and respond', desc: 'When the AI flags something, a person reviews it and decides what happens next.',
    subs: [{id: 'Live map', hosted: true, src: '/command/?embedded=1'}, {id: 'Review queue'}, {id: 'Cameras'}]},
  {id: 'Plan', icon: MapPin, title: 'Plan ahead', desc: 'Where to station ambassadors, and which walking routes are safer.',
    note: "Patrol and routes uses Palladium's own incident history. Forecast and history uses public LAPD reports.",
    subs: [{id: 'Patrol and routes', hosted: true, src: '/command/?embedded=1#/trends'}, {id: 'Forecast and history'}, {id: 'Past incidents', hosted: true, src: '/command/?embedded=1#/incidents'}]},
  {id: 'Learn', icon: BookOpen, title: 'Learn from reviewed clips', desc: 'Reviewed clips, with permission, become training examples that improve the model.', subs: [{id: 'Learn'}]},
  {id: 'Privacy', icon: Lock, title: 'Privacy and data', desc: 'What is kept, for how long, and who can see it.', subs: [{id: 'Privacy'}]},
];
const visibleSubs = (s: Section) => s.subs.filter(x => !x.hosted || hasCommandCenter);
const defaultSub = (s: Section) => visibleSubs(s)[0].id;
const commandViews = sections.flatMap(s => s.subs.filter(x => x.src).map(x => ({key: s.id + '/' + x.id, src: x.src!, title: x.id})));
// Landing view, plus the old deep links (#activity, #learning).
const initNav = (() => {
  const section = location.hash === '#learning' ? 'Learn' : location.hash === '#activity' ? 'Plan' : hasCommandCenter ? 'Respond' : 'Home';
  const subs: Record<string, string> = Object.fromEntries(sections.map(s => [s.id, defaultSub(s)]));
  if (location.hash === '#activity') subs.Plan = 'Forecast and history';
  const frame = sections.find(s => s.id === section)!.subs.find(x => x.id === subs[section])?.src ? section + '/' + subs[section] : '';
  return {section, subs, frame};
})();

function CommandCenter({visible,src,title}:{visible:boolean,src:string,title:string}){
  const observer=useRef<ResizeObserver|null>(null);
  useEffect(()=>()=>observer.current?.disconnect(),[]);
  return <iframe
    className="command-frame"
    title={title}
    src={src}
    hidden={!visible}
    onLoad={({currentTarget:frame})=>{
      observer.current?.disconnect();
      const content=frame.contentDocument?.body;
      if(!content)return;
      observer.current=new ResizeObserver(([entry])=>{
        // A hidden tab has zero size. Keep its last height until it is shown again.
        if(entry.contentRect.height>0)frame.style.height=Math.ceil(entry.borderBoxSize[0].blockSize)+'px';
      });
      observer.current.observe(content);
    }}
  />;
}

function App(){
  const [bookmarkNotice,setBookmarkNotice]=useState('');
  const seenIncidents=useRef(new Set<string>());
  const [reviewAlert,setReviewAlert]=useState<AnyRecord|null>(null),[notificationPermission,setNotificationPermission]=useState('Notification' in window?Notification.permission:'unavailable');
  const [authed,setAuthed]=useState(false),[token,setToken]=useState(''),[sources,setSources]=useState<AnyRecord[]>([]),[incidents,setIncidents]=useState<AnyRecord[]>([]),[health,setHealth]=useState<AnyRecord>({}),[selected,setSelected]=useState<string|null>(null),[pair,setPair]=useState(''),[error,setError]=useState(''),[uploading,setUploading]=useState(false),[connected,setConnected]=useState(false),[section,setSection]=useState(initNav.section),[subs,setSubs]=useState<Record<string,string>>(initNav.subs)
  const [commandOpened,setCommandOpened]=useState<string[]>(initNav.frame?[initNav.frame]:[]);
  const openIncident=(id:string)=>{setSection('Respond');setSubs(p=>({...p,Respond:'Review queue'}));setSelected(id)};
  const go=(sectionId:string,subId?:string)=>{const sec=sections.find(x=>x.id===sectionId)!,target=subId||defaultSub(sec),key=sectionId+'/'+target;if(sec.subs.find(x=>x.id===target)?.src)setCommandOpened(o=>o.includes(key)?o:[...o,key]);setSection(sectionId);setSubs(p=>({...p,[sectionId]:target}));setSelected(null);history.replaceState(null,'',sectionId==='Learn'?'#learning':key==='Plan/Forecast and history'?'#activity':location.pathname+location.search)};
  const refresh=async()=>{try{const [s,i,h]=await Promise.all([api('/sources'),api('/incidents'),api('/health')]);i.forEach((incident:AnyRecord)=>seenIncidents.current.add(incident.id));setSources(s);setIncidents(i);setHealth(h);setAuthed(true)}catch(e:any){if(e.status===401)setAuthed(false);else setConnected(false)}};
  useEffect(()=>{refresh()},[]);
  useEffect(()=>{if(!authed)return;let ws:WebSocket;let retry:number;let stopped=false;
    const connect=()=>{ws=new WebSocket(wsUrl('/ws/events'));ws.onopen=()=>setConnected(true);ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='source')setSources(old=>[m.payload,...old.filter(s=>s.id!==m.payload.id)]);if(m.type==='incident'){
      if(!seenIncidents.current.has(m.payload.id)){
        seenIncidents.current.add(m.payload.id);setReviewAlert(m.payload);
        if('Notification' in window&&Notification.permission==='granted'&&document.hidden){
          const notice=new Notification(m.payload.record?.trigger_origin==='reviewer'?'Palladium · Marked moment saved':'Palladium · Review requested',{body:'A possible safety event has supporting footage. Open the original clip and assess the context.',tag:m.payload.id});
          notice.onclick=()=>{window.focus();openIncident(m.payload.id);setReviewAlert(null);notice.close()};
        }
      }
      setIncidents(old=>[m.payload,...old.filter(i=>i.id!==m.payload.id)])
    }};ws.onclose=()=>{setConnected(false);if(!stopped)retry=window.setTimeout(connect,2000)}};connect();
    const poll=setInterval(()=>{refresh()},2500);return()=>{stopped=true;clearInterval(poll);clearTimeout(retry);ws?.close()};
  },[authed]);
  async function pairPhone(){try{setError('');const r=await post('/sources',{name:'Mobile camera',kind:'phone'});const capture=new URL('/capture',window.location.origin);capture.hash=new URL(r.capture_url,window.location.origin).hash;setPair(capture.href);refresh()}catch(e){setError(String(e))}}
  async function upload(file:File){setUploading(true);setError('');try{const body=new FormData();body.append('file',file);await api('/upload',{method:'POST',body});refresh()}catch(e){setError(String(e))}finally{setUploading(false)}}
  async function markMoment(sourceId:string){try{await post(`/sources/${sourceId}/bookmark`,{});setBookmarkNotice('Moment marked. Surrounding footage will appear for review after its context is saved.')}catch(e){setError(String(e))}}
  async function exportFeedback(){try{const data=await api('/reviews/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='safesc-review-history.json';link.click();URL.revokeObjectURL(url)}catch(e){setError(String(e))}}
  if(!authed)return <><div className="login-theme"><ThemeToggle/></div><div className="login"><div className="brand-lockup"><Logo variant="shield" size={64}/><span className="wordmark">PALLADIUM</span></div><h1>A clearer view.<br/>A human decision.</h1><p>Campus safety where AI watches and people decide. Enter your access token to continue.</p><form onSubmit={async e=>{e.preventDefault();try{await post('/login',{token});refresh()}catch(e){setError(String(e))}}}><input type="password" autoComplete="current-password" aria-label="Workspace access token" placeholder="Workspace access token" value={token} onChange={e=>setToken(e.target.value)}/><button className="primary">Open workspace <ArrowUpRight size={18}/></button></form>{error&&<p className="error">{error}</p>}<small>Access token: data/app_token on the server</small></div></>;
  const source=sources.find(s=>s.status==='streaming')||sources[0],pending=incidents.filter(i=>i.review_status==='pending').length;
  const sec=sections.find(x=>x.id===section)!,vsubs=visibleSubs(sec),sub=vsubs.some(x=>x.id===subs[section])?subs[section]:vsubs[0].id,viewKey=section+'/'+sub,isFrame=!!sec.subs.find(x=>x.id===sub)?.src;
  const statsEl=<div className="stats"><div><span>Connected cameras</span><strong>{sources.filter(s=>s.status==='streaming').length}<small> / 1</small></strong><p><i className="dot green"/>Analysis running</p></div><div><span>Waiting for review</span><strong>{pending.toString().padStart(2,'0')}</strong><p>A person needs to look</p></div><div><span>AI model</span><strong className="model-name">{health.streaming_model==='moss'?'MOSS-VL':health.streaming_model||'—'}</strong><p>{health.gpu?.[0]||'GPU not connected'}</p></div><div><span>Evidence integrity</span><strong className="model-name">SHA-256</strong><p><Lock size={12}/>Original clips fingerprinted</p></div></div>;
  const queueEl=<section className="queue"><div className="section-heading"><div><h2>Review queue <span>{incidents.length}</span></h2><p>Possible safety events, ready for a person to review.</p></div><span className="fine">{uploading?'Uploading video…':'Newest first'}</span></div>{!incidents.length?<div className="empty-queue"><Check size={26}/><h3>No incidents are waiting.</h3><p>When the AI flags something, it appears here for a person to review.</p></div>:<div className="incident-grid">{incidents.map(i=><button className="incident-card" key={i.id} onClick={()=>openIncident(i.id)}><div className="card-image"><img src={media(i.id,'thumbnail')} alt="Incident thumbnail"/><span className="pill amber">{i.simulated?'SIMULATED':[incidentLabel(i.record),i.severity.replaceAll('_',' ')].filter(Boolean).join(' · ')}</span></div><div className="card-copy"><small><Clock size={12}/>{new Date(i.created_at).toLocaleTimeString()}<span>{i.review_status.replace('_',' ')}</span></small><h3>{i.record.observable_events[0]?.observation||'Possible safety incident'}</h3><p>{(i.end_time-i.start_time).toFixed(1)}s preserved · {i.record.lighting_condition.replaceAll('_',' ')}</p><div>Review evidence <ArrowUpRight size={17}/></div></div></button>)}</div>}</section>;
  const showStatus=!isFrame&&(section==='Home'||section==='Respond'||section==='Privacy');
  return <div className="shell"><aside><div className="brand-lockup"><Logo variant="mark" size={30}/><div><span className="wordmark">PALLADIUM</span><span className="tagline">Campus safety</span></div></div><nav className="nav-list" aria-label="Palladium sections">{sections.map(({id,icon:Icon})=><button key={id} aria-label={id} aria-current={section===id?'page':undefined} className={'nav '+(section===id?'active':'')} onClick={()=>go(id)}><Icon size={18}/>{id}{id==='Respond'&&pending>0&&<b>{pending}</b>}</button>)}</nav><div className="sidebar-theme"><ThemeToggle/><span>Appearance</span></div><div className="sidebar-bottom"><Shield size={20}/><p>AI observes.<br/><strong>People decide.</strong></p><small>No identity recognition.<br/>No automatic dispatch.</small></div></aside>
    <div className="main"><header><span>Palladium <span className="slash">/</span> {section}</span><div>{notificationPermission!=='unavailable'&&<button className="text-button" title="Notify this reviewer while the workspace is open. No campus-wide alerts are sent." onClick={async()=>{setNotificationPermission(await Notification.requestPermission())}}><Bell size={16}/>{notificationPermission==='granted'?'Review alerts on':'Enable review alerts'}</button>}<i className={'dot '+(connected?'green':'')}/>{connected?'Workspace connected':'Reconnecting'}<span className="avatar">SC</span></div></header><main>
    {reviewAlert&&<div className="review-notification" role="status"><AlertTriangle size={20}/><div><strong>{reviewAlert.record?.trigger_origin==='reviewer'?'Marked moment saved · review required':'Possible incident · review required'}</strong><p>Open the original footage, assess the uncertainty, then confirm or dismiss.</p></div><button onClick={()=>{openIncident(reviewAlert.id);setReviewAlert(null)}}>Review footage <ArrowUpRight size={16}/></button><button className="text-button" aria-label="Dismiss notification" onClick={()=>setReviewAlert(null)}><X size={16}/></button></div>}
    <div className="page-head"><div className="page-head-top"><div><span className="eyebrow">{section}</span><h1>{sec.title}</h1><p>{sec.desc}</p></div>{(section==='Home'||section==='Respond')&&!isFrame&&<button onClick={pairPhone}><Smartphone size={17}/>Connect phone</button>}</div>
      {vsubs.length>1&&<div className="subtabs" role="tablist" aria-label={section+' views'}>{vsubs.map(x=><button key={x.id} role="tab" aria-selected={sub===x.id} className={'subtab '+(sub===x.id?'selected':'')} onClick={()=>go(section,x.id)}>{x.id}{x.id==='Review queue'&&pending>0&&<b>{pending}</b>}</button>)}</div>}
      {vsubs.length>1&&sec.note&&<p className="subtabs-note">{sec.note}</p>}</div>
    {commandViews.filter(v=>commandOpened.includes(v.key)).map(v=><CommandCenter key={v.key} src={v.src} title={v.title} visible={viewKey===v.key&&!selected}/>)}
    {showStatus&&<>{error&&<div className="error" role="alert">{error}</div>}{health.verifier_research_mode&&<div className="simulation">HIGH-RECALL RESEARCH CANDIDATE · Owner-approved staged demo · Human review required. See Learn for measured misses and false alerts.</div>}{health.simulated&&<div className="simulation">SIMULATED DEVELOPMENT MODE · Fixture alerts are not real model results.</div>}</>}
    {showStatus&&source?.status==='streaming'&&section!=='Privacy'&&<div className="live-actions"><button onClick={()=>markMoment(source.id)}><Eye size={16}/>Mark moment for review</button><span className="fine">Save context when you see something that deserves a closer look.</span></div>}
    {showStatus&&bookmarkNotice&&<p className="fine" role="status">{bookmarkNotice}</p>}
    {selected?<ReviewPage id={selected} onBack={()=>setSelected(null)}/>:isFrame?null:section==='Learn'?<React.Suspense fallback={<p>Loading learning repository…</p>}><LearningRepository/></React.Suspense>:viewKey==='Plan/Forecast and history'?<div className="plan-view"><React.Suspense fallback={<p>Loading forecast and history…</p>}><ActivityMap/></React.Suspense></div>:section==='Privacy'?<>
    <p><button onClick={exportFeedback}>Export review history</button><span className="fine"> Reviewer feedback is auditable. Model training requires a separate evaluated release.</span></p>
    <StorageStatus/><div className="controls-grid">{[['Originals preserved','Untouched uploads and hashed incident windows are stored separately from generated review copies.'],['People stay anonymous','Only temporary person labels are used. Identity and demographic inference are excluded.'],['Human review required','Confirm, dismiss or request more review. No external dispatch occurs.'],['Short retention',`Incident media expires after ${health.retention_hours||24} hours. Reviewer decisions remain auditable.`],['Honest processing','Unavailable models report errors. Fixtures and enhanced videos carry visible labels.'],['Secure phone capture','Pairing links expire in one hour. Camera capture requires HTTPS; audio stays off.']].map(([title,copy])=><section className="panel" key={title}><Lock size={20}/><h3>{title}</h3><p>{copy}</p></section>)}</div></>:viewKey==='Respond/Review queue'?<>{statsEl}{queueEl}</>:viewKey==='Respond/Cameras'?<LiveMonitor source={source} onPair={pairPhone} onUpload={upload} onOpen={openIncident}/>:<>
    <ol className="how-steps" aria-label="How Palladium works">{['AI watches camera feeds and flags possible incidents','A person reviews the evidence and decides','Palladium alerts people nearby and sends the closest units'].map((t,n)=><li key={t}><b>{n+1}</b><span>{t}</span></li>)}</ol>
    {statsEl}</>}
    <footer><Shield size={14}/>Palladium is a human decision-support prototype.<span>Built for Trojans, by Trojans.</span></footer></main></div>
    {pair&&<div className="modal-backdrop" onClick={()=>setPair('')}><section className="pair-modal" onClick={e=>e.stopPropagation()}><button className="close" onClick={()=>setPair('')} aria-label="Close pairing"><X/></button><Smartphone size={30}/><h2>Bring your phone into view.</h2><p>Scan with your phone camera. Open the link, allow camera access, then tap Start camera.</p><div className="qr"><QRCodeSVG value={pair} size={210}/></div><input readOnly aria-label="Phone pairing URL" value={pair}/><button className="primary wide" onClick={()=>navigator.clipboard.writeText(pair)}>Copy pairing link</button><p className="fine">Link expires in 1 hour. HTTPS is required on your phone. Keep the capture page open.</p></section></div>}</div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode>{location.pathname==='/capture'?<Capture/>:<App/>}</React.StrictMode>);
