import React,{useState,useEffect,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity,ArrowUpRight,Bell,Camera,Check,ChevronLeft,Clock,Eye,FileVideo,Layers,Lock,Radio,Shield,Smartphone,Upload,X,Sun,AlertTriangle,MapPin,BookOpen} from 'lucide-react';
import {QRCodeSVG} from 'qrcode.react';
import './style.css';
import LiveMonitor from './LiveMonitor';
import {publishLiveVideo} from './liveVideo';
import {recordEvidence,type RecordingHandle} from './nativeRecording';
import {shareCameraLocation,type CameraLocationState} from './geolocation';
import {Logo} from './Logo';
import WorkspaceBoundary from './WorkspaceBoundary';
import PhoneAlerts,{type PhoneAlert} from './PhoneAlerts';
import {readNavigation,navigationHash,type WorkspaceTab} from './workspaceNavigation.mjs';
import {WorkspaceStateProvider,useWorkspaceState} from './WorkspaceState';
const ReviewPage=React.lazy(()=>import('./IncidentReview'));
import StorageStatus from './StorageStatus';
import IncidentQueue from './IncidentQueue';
import {updateIncidentQueue} from './incidentQueueState.mjs';
import {responseRequested} from './responseState.mjs';
const LearningRepository=React.lazy(()=>import('./LearningRepository'));
const ActivityMap=React.lazy(()=>import('./ActivityMap'));
const IncidentResponse=React.lazy(()=>import('./IncidentResponse'));
const LearningPipeline=React.lazy(()=>import('./LearningPipeline'));

type AnyRecord=Record<string,any>;
async function api(path:string,options:RequestInit={}){
  const r=await fetch('/api'+path,options);
  if(!r.ok){let detail='Request failed';try{detail=(await r.json()).detail}catch{}throw Object.assign(Error(typeof detail==='string'?detail:JSON.stringify(detail)),{status:r.status})}return r.json();
}
const post=(path:string,body:unknown)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const wsUrl=(path:string)=>`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${path}`;
const media=(id:string,kind:string)=>`/api/incidents/${id}/media/${kind}`;

function Capture(){
  const [phoneAlert,setPhoneAlert]=useState<PhoneAlert|null>(null);
  const video=useRef<HTMLVideoElement>(null),socket=useRef<WebSocket|null>(null),stream=useRef<MediaStream|null>(null),timer=useRef<number>(0),running=useRef(false),wake=useRef<any>(null),liveVideo=useRef<(()=>void)|null>(null),recording=useRef<RecordingHandle|null>(null),locationShare=useRef<(()=>void)|null>(null),locationEnabled=useRef(true),captureReady=useRef(false),locationSupported=useRef(false);
  const [status,setStatus]=useState('Ready to connect'),[frames,setFrames]=useState(0),[error,setError]=useState(''),[active,setActive]=useState(false),[facing,setFacing]=useState('environment'),[quality,setQuality]=useState('fast'),[captureInfo,setCaptureInfo]=useState(''),[liveStatus,setLiveStatus]=useState(''),[recordingStatus,setRecordingStatus]=useState(''),[shareLocation,setShareLocation]=useState(true),[locationState,setLocationState]=useState<CameraLocationState|null>(null),[locationAck,setLocationAck]=useState('');
  function startLocation(){const ws=socket.current;if(!locationEnabled.current||!captureReady.current||!ws||ws.readyState!==WebSocket.OPEN)return;if(!locationSupported.current){setLocationState({status:'unavailable',message:'This server does not support geotagging yet. Camera video continues.'});return}locationShare.current?.();setLocationAck('');locationShare.current=shareCameraLocation(message=>{if(ws===socket.current&&ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify(message))},setLocationState)}
  function toggleLocation(enabled:boolean){locationEnabled.current=enabled;setShareLocation(enabled);locationShare.current?.();locationShare.current=null;setLocationState(null);setLocationAck('');if(enabled)startLocation()}
  const stop=async()=>{locationShare.current?.();locationShare.current=null;captureReady.current=false;locationSupported.current=false;setLocationState(null);setLocationAck('');running.current=false;clearTimeout(timer.current);const ws=socket.current;socket.current=null;liveVideo.current?.();liveVideo.current=null;const native=recording.current;recording.current=null;const flushed=native?.stop();stream.current?.getTracks().forEach(t=>t.stop());wake.current?.release();setActive(false);setStatus('Camera stopped');setLiveStatus('');await flushed;if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'stop'}));else ws?.close()};
  useEffect(()=>()=>{void stop()},[]);
  async function start(){
    await stop();setError('');setPhoneAlert(null);setStatus('Opening camera…');
    try{
      if(!window.isSecureContext)throw Error('Camera access needs HTTPS. Open the secure pairing URL from the dashboard.');
      if(!navigator.mediaDevices?.getUserMedia)throw Error('This browser does not support camera capture. Use Safari or Chrome.');
      const token=location.hash.slice(1);if(!token)throw Error('Open the pairing link from the Palladium dashboard first.');
      stream.current=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:facing},width:{ideal:1920},height:{ideal:1080},frameRate:{ideal:60,max:60}},audio:false});
      const camera=stream.current.getVideoTracks()[0];
      const actual=camera.getSettings();setCaptureInfo(`${actual.width||'?'} × ${actual.height||'?'} · ${actual.frameRate?.toFixed(0)||'?'} capture FPS`);
      video.current!.srcObject=stream.current;await video.current!.play();
      liveVideo.current=publishLiveVideo(stream.current,token,setLiveStatus);
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
      ws.onopen=()=>{setStatus('Pairing with Palladium…');ws.send(JSON.stringify({token,native_recording:typeof MediaRecorder!=='undefined'}))};
      ws.onmessage=e=>{if(ws!==socket.current)return;const m=JSON.parse(e.data);if(m.type==='ready'){captureReady.current=true;locationSupported.current=m.geolocation===true;startLocation();fps=Math.min(m.fps,quality==='fast'?12:8);setStatus('Connected · streaming');if(m.native_recording)recording.current=recordEvidence(stream.current!,m.source_id,token,setRecordingStatus);else setRecordingStatus('Evidence clips use sampled frames · smooth native recording is not enabled');send()}
        if(m.type==='phone_alert'){setPhoneAlert(m.alert);return}
        if(m.type==='geolocation'){setLocationAck(m.accepted&&m.status==='active'?'Location shared with the reviewer':m.status==='disabled'?'Location sharing is off.':m.reason==='rate_limited'?'Waiting to share the next location fix.':m.reason&&m.reason!=='unchanged'?'Location was not accepted yet; waiting for a fresh fix.':'');return}
        if(m.type==='ack'){inFlight=Math.max(0,inFlight-1);setFrames(m.index);setStatus(m.model_status==='error'?'Streaming · model unavailable':m.model_status==='waiting_for_gpu'?'Streaming · analysis queued':m.model_status==='loading'?'Streaming · analysis starting':m.model_status==='verifying'?'Streaming · verifying video':m.model_status==='confirming_event'?'Streaming · checking event context':'Connected · streaming')}
        if(m.type==='error'){setError(m.message);stop()}};
      ws.onclose=e=>{if(ws===socket.current&&running.current){setStatus('Disconnected');setError(e.reason||'Connection closed. Tap Start camera to reconnect.');stop()}};
      ws.onerror=()=>setError('Unable to reach server. Check your connection and secure pairing URL.');
      try{wake.current=await (navigator as any).wakeLock?.request('screen')}catch{}
    }catch(e){setError(String(e));stop();setStatus('Not connected')}
  }
  return <div className="capture-page"><div className="brand"><Logo size={34}/><strong>Palladium</strong><small>CAMERA LINK</small></div><h1>Your phone.<br/>A clearer view.</h1><p>Secure camera link to your Palladium review workspace. Audio is off.</p>
    {error&&<div className="error" role="alert">{error}</div>}
    <section className="camera-location"><label><input type="checkbox" checked={shareLocation} onChange={e=>toggleLocation(e.target.checked)}/><span>Share camera location<small>Allow location when the browser asks. Its reported accuracy is saved with new events. Uncheck to stream without GPS.</small></span></label><p role="status">{active&&locationState?<>{locationState.message}{locationState.status==='active'&&locationAck&&<span>{locationAck}</span>}</>:shareLocation?'Location will be requested after the camera connects.':'Location sharing is off.'}</p>{active&&locationState&&['denied','unavailable'].includes(locationState.status)&&<button onClick={startLocation}>Retry phone location</button>}</section>
    {!active?<button className="primary wide capture-start" onClick={start}><Camera size={18}/>Start camera</button>:<button className="danger wide capture-start" onClick={stop}>Stop streaming</button>}
    <div className="phone-preview"><video ref={video} autoPlay playsInline muted/>{!active&&<Camera size={48}/>}<span className="phone-status"><i className={active?'dot green':'dot'}/>{status}</span></div>
    <div className="capture-stats"><span>{frames} evidence frames acknowledged</span><span>{captureInfo||`${facing==='environment'?'Rear':'Front'} camera · target 1080p / 60 FPS`}</span></div>
    {liveStatus&&<p className="fine" role="status">{liveStatus}</p>}{recordingStatus&&<p className="fine" role="status">{recordingStatus}</p>}
    <PhoneAlerts alert={phoneAlert} active={active}/>

    {!active&&<details className="capture-settings"><summary>Camera settings</summary><select aria-label="Camera" value={facing} onChange={e=>setFacing(e.target.value)}><option value="environment">Rear camera</option><option value="user">Front camera</option></select><select aria-label="Analysis sample quality" value={quality} onChange={e=>setQuality(e.target.value)}><option value="fast">Analysis samples · 960px · up to 12 FPS</option><option value="detail">Analysis samples · 1280px · up to 8 FPS</option></select></details>}
    <p className="fine">The dashboard receives the camera video directly with a 1080p / 60 FPS target. Actual quality depends on device and network. When enabled on the server and supported by this browser, native recording preserves smoother new incident clips. The recording status above shows which mode is active; otherwise saved clips use the sampled evidence frames. Keep this page open and the phone awake. Landscape gives the clearest event view. Works over Wi-Fi or cellular. If the connection drops, reconnect here; a new stream starts at zero.</p></div>
}


function incidentFromLocation(){return readNavigation(location.hash).incident}
function App(){
  const [bookmarkNotice,setBookmarkNotice]=useState(''),[warmNotice,setWarmNotice]=useState(''),[warming,setWarming]=useState(false);
  const seenIncidents=useRef(new Set<string>()),eventConnected=useRef(false),sourceRevision=useRef(0),incidentRevision=useRef(0);
  const [removedIncident,setRemovedIncident]=useState<AnyRecord|null>(null);
  const applyIncident=(incident:AnyRecord)=>{incidentRevision.current++;seenIncidents.current.add(incident.id);setIncidents(old=>updateIncidentQueue(old,incident));if(incident.record?.queue_deleted?.active){setRemovedIncident(incident);setReviewAlert(old=>old?.id===incident.id?null:old)}else if(incident.record?.queue_deleted?.active===false){setRemovedIncident(old=>old?.id===incident.id?null:old)}};
  const [reviewAlert,setReviewAlert]=useState<AnyRecord|null>(null),[notificationPermission,setNotificationPermission]=useState('Notification' in window?Notification.permission:'unavailable');
  const [authed,setAuthed]=useState(false),[token,setToken]=useState(''),[sources,setSources]=useState<AnyRecord[]>([]),[incidents,setIncidents]=useState<AnyRecord[]>([]),[health,setHealth]=useState<AnyRecord>({}),[selected,setSelected]=useState<string|null>(incidentFromLocation),[pair,setPair]=useState(''),[error,setError]=useState(''),[uploading,setUploading]=useState(false),[connected,setConnected]=useState(false),[tab,setTab]=useState<WorkspaceTab>(()=>readNavigation(location.hash).tab);
  const refresh=async()=>{const revision=sourceRevision.current,incidentVersion=incidentRevision.current;try{const [s,i,h]=await Promise.all([api('/sources'),api('/incidents'),api('/health')]);i.forEach((incident:AnyRecord)=>seenIncidents.current.add(incident.id));setSources(old=>revision===sourceRevision.current?s:[...old,...s.filter((source:AnyRecord)=>!old.some(item=>item.id===source.id))]);if(incidentVersion===incidentRevision.current)setIncidents(i);setHealth(h);setAuthed(true)}catch(e:any){if(e.status===401)setAuthed(false);else setConnected(false)}};
  useEffect(()=>{refresh()},[]);
  const {setCampus,setResponseIncident,setResponseRuns,setLearningIncident,setLearningDemos}=useWorkspaceState();
  function navigate(next:WorkspaceTab){
    setTab(next);setSelected(null);window.scrollTo({top:0,behavior:'auto'});
    if(next==='ambassadors'||next==='routes')setCampus(old=>({...old,mode:next==='ambassadors'?'posts':['routes','posts'].includes(old.mode)?old.mode:'routes'}));
    if(next==='activity')setCampus(old=>({...old,mode:'explore'}));
    history.pushState(null,'',location.pathname+location.search+navigationHash(next));
  }
  function openResponse(incident:AnyRecord,simulate=false){applyIncident(incident);setResponseIncident(incident.id);if(simulate&&responseRequested(incident))setResponseRuns(old=>({...old,[incident.id]:'notified'}));navigate('response');}
  function openLearning(incident:AnyRecord){applyIncident(incident);setLearningIncident(incident.id);setLearningDemos(old=>old[incident.id]?old:{...old,[incident.id]:{elapsed:0,playing:true,selectedRole:null}});navigate('pipeline');}
  function openIncident(id:string){setSelected(id);setTab('incidents');window.scrollTo({top:0,behavior:'auto'});history.pushState(null,'',location.pathname+location.search+navigationHash('incidents',id));}
  useEffect(()=>{const sync=()=>{const next=readNavigation(location.hash);setSelected(next.incident);setTab(next.tab);if(next.tab==='ambassadors'||next.tab==='routes')setCampus(old=>({...old,mode:next.tab==='ambassadors'?'posts':['routes','posts'].includes(old.mode)?old.mode:'routes'}));};window.addEventListener('hashchange',sync);window.addEventListener('popstate',sync);return()=>{window.removeEventListener('hashchange',sync);window.removeEventListener('popstate',sync)}},[setCampus]);
  useEffect(()=>{if(!authed)return;let ws:WebSocket;let retry:number;let stopped=false;
    const connect=()=>{ws=new WebSocket(wsUrl('/ws/events'));ws.onopen=()=>{eventConnected.current=true;setConnected(true);refresh()};ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='source'){sourceRevision.current++;setSources(old=>[m.payload,...old.filter(s=>s.id!==m.payload.id)])};if(m.type==='incident'){
      if(!m.payload.record?.queue_deleted?.active&&!m.payload.record?.queue_deleted?.restored_at&&!seenIncidents.current.has(m.payload.id)){
        seenIncidents.current.add(m.payload.id);setReviewAlert(m.payload);
        if('Notification' in window&&Notification.permission==='granted'&&document.hidden){
          const notice=new Notification(m.payload.record?.trigger_origin==='reviewer'?'Palladium · Marked moment saved':'Palladium · Review requested',{body:'A possible safety event has supporting footage. Open the original clip and assess the context.',tag:m.payload.id});
          notice.onclick=()=>{window.focus();openIncident(m.payload.id);setReviewAlert(null);notice.close()};
        }
      }
      applyIncident(m.payload)
    }if(m.type==='job'&&['completed','failed','queued','running'].includes(m.payload.status)){const version=incidentRevision.current;api('/incidents').then(list=>{if(version===incidentRevision.current)setIncidents(list)}).catch(()=>{})}};ws.onclose=()=>{eventConnected.current=false;setConnected(false);if(!stopped)retry=window.setTimeout(connect,2000)}};connect();
    const poll=setInterval(()=>{if(eventConnected.current)api('/health').then(setHealth).catch(()=>{});else refresh()},2500);return()=>{stopped=true;eventConnected.current=false;clearInterval(poll);clearTimeout(retry);ws?.close()};
  },[authed]);
  async function prepareLiveModels(){if(warming||health.streaming_model!=='moss')return;setWarming(true);setWarmNotice('Preparing MOSS and visual verification before capture…');try{await post('/models/moss/warm',{});setWarmNotice('Live models ready · camera capture can begin.');refresh()}catch(e:any){setWarmNotice(e.status===409?'Live analysis is already using the GPU.':`Model preparation needs attention: ${e.message}`)}finally{setWarming(false)}}
  async function pairPhone(){if(!sourceBusy)void prepareLiveModels();try{setError('');const r=await post('/sources',{name:'Mobile camera',kind:'phone'});const capture=new URL('/capture',window.location.origin);capture.hash=new URL(r.capture_url,window.location.origin).hash;setPair(capture.href);refresh()}catch(e){setError(String(e))}}
  async function upload(file:File){setUploading(true);setError('');try{const body=new FormData();body.append('file',file);await api('/upload',{method:'POST',body});refresh()}catch(e){setError(String(e))}finally{setUploading(false)}}
  async function markMoment(sourceId:string){try{await post(`/sources/${sourceId}/bookmark`,{});setBookmarkNotice('Moment marked. Surrounding footage will appear for review after its context is saved.')}catch(e){setError(String(e))}}
  async function exportFeedback(){try{const data=await api('/reviews/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='safesc-review-history.json';link.click();URL.revokeObjectURL(url)}catch(e){setError(String(e))}}
  if(!authed)return <div className="login"><div className="brand"><Logo variant="shield" size={52}/><strong>Palladium</strong></div><span className="eyebrow">Campus review & planning</span><h1>A clearer view.<br/>A considered response.</h1><p>Video analysis, incident response illustrations, and a more informed way across campus. Your review workspace starts here.</p><form onSubmit={async e=>{e.preventDefault();try{await post('/login',{token});refresh()}catch(e){setError(String(e))}}}><label htmlFor="workspace-token">Workspace access token</label><input id="workspace-token" type="password" autoComplete="current-password" placeholder="Enter your access token" value={token} onChange={e=>setToken(e.target.value)}/><button className="primary">Open workspace <ArrowUpRight size={17}/></button></form>{error&&<p className="error" role="alert">{error}</p>}<small>Authorized reviewer access · audio is off · human decisions</small></div>;
  const source=sources.find(s=>['streaming','processing','warming','waiting_for_gpu'].includes(s.status))||sources[0];
  const pending=incidents.filter(i=>['pending','needs_review'].includes(i.review_status));
  const sourceBusy=sources.some(s=>['streaming','processing','warming','waiting_for_gpu'].includes(s.status));
  const names:Record<WorkspaceTab,string>={live:'Real-time analysis',response:'Incident response',ambassadors:'Get Somewhere',routes:'Get Somewhere',incidents:'Incidents',activity:'Campus outlook',pipeline:'Learning pipeline',learning:'Learning repository',controls:'Controls'};
  const primary:[WorkspaceTab,string][]=[['live','Real-time analysis'],['response','Incident response'],['routes','Get Somewhere'],['pipeline','Learning pipeline']];
  return <div className="app-shell">
    <header className="app-header"><a className="brand" href="#live" onClick={e=>{e.preventDefault();navigate('live')}}><Logo size={38}/><strong>Palladium</strong></a>
      <nav className="primary-nav" aria-label="Solutions">{primary.map(([id,label],i)=><a key={id} href={navigationHash(id)} aria-current={!selected&&(tab===id||id==='routes'&&tab==='ambassadors')?'page':undefined} className={!selected&&(tab===id||id==='routes'&&tab==='ambassadors')?'selected':''} onClick={e=>{e.preventDefault();navigate(id)}}><span className="nav-number">0{i+1}</span>{label}</a>)}</nav>
      <div className="utility-nav"><a className={tab==='incidents'?'selected':''} href="#incidents" onClick={e=>{e.preventDefault();navigate('incidents')}}>Incidents{pending.length>0&&<b>{pending.length}</b>}</a><details className="more-nav"><summary>More</summary><div>{(['activity','learning','controls'] as WorkspaceTab[]).map(id=><button key={id} onClick={e=>{navigate(id);e.currentTarget.closest('details')?.removeAttribute('open')}}>{names[id]}</button>)}</div></details></div>
    </header>
    <div className="workspace-status"><span><i className={'dot '+(connected?'green':'')}/>{connected?'Workspace connected':'Reconnecting to backend'}</span><span>Human review workspace <span className="status-separator">/</span> {selected?'Incident review':names[tab]}</span><button className="text-button" onClick={pairPhone}><Smartphone size={13}/>Pair phone</button></div>
    <main className="workspace-main">
      {error&&<div className="error" role="alert">{error}<button className="text-button" onClick={()=>setError('')} aria-label="Dismiss error"><X size={14}/></button></div>}
      {health.verifier_research_mode&&<details className="research-note"><summary>Research model · human review required</summary><p>Owner-approved staged research candidate. Scores are uncalibrated; see Learning for measured misses and false alerts.</p></details>}
      {health.simulated&&<div className="simulation">Simulated development mode · fixture alerts are not real model results.</div>}
      {reviewAlert&&<div className="review-notification" role="status"><AlertTriangle size={20}/><div><strong>{reviewAlert.record?.trigger_origin==='reviewer'?'Marked moment saved':'Possible incident · review required'}</strong><p>Original footage and context are ready for a closer look.</p></div><button onClick={()=>{openIncident(reviewAlert.id);setReviewAlert(null)}}>Review footage <ArrowUpRight size={15}/></button><button className="text-button" aria-label="Dismiss notification" onClick={()=>setReviewAlert(null)}><X size={16}/></button></div>}
      <React.Suspense fallback={<section className="panel unavailable" role="status">Loading workspace…</section>}>
        {selected?<ReviewPage id={selected} onLearning={openLearning} onResponse={openResponse} onBack={()=>navigate('incidents')} onChange={applyIncident} onDeleted={incident=>{applyIncident(incident);navigate('incidents')}}/>:
          tab==='pipeline'?<LearningPipeline incidents={incidents} onOpen={openIncident} onRepository={()=>navigate('learning')}/>:
          tab==='learning'?<LearningRepository onReturnToLive={()=>navigate('live')}/>:
          tab==='response'?<IncidentResponse incidents={incidents} sources={sources} onOpen={openIncident} onChange={applyIncident}/>:
          ['activity','ambassadors','routes'].includes(tab)?<ActivityMap mode={tab==='ambassadors'?'posts':undefined} planningOnly={tab!=='activity'} onModeChange={mode=>{if(mode==='posts'||mode==='routes')navigate('routes')}}/>:
          tab==='controls'?<>
            <div className="page-heading"><div><span className="eyebrow">Workspace controls</span><h1>Context, care, and a record.</h1><p>Manage retention, review history, and this reviewer’s notifications.</p></div><button onClick={exportFeedback}>Export review history</button></div>
            <section className="panel notification-controls"><Bell size={20}/><div><h2>Reviewer notifications</h2><p>Notifications are for this signed-in reviewer while the workspace is open. Paired phones receive flags from their own camera while capture is open. No dispatch or SMS service is connected.</p></div>{notificationPermission!=='unavailable'&&<button onClick={async()=>setNotificationPermission(await Notification.requestPermission())}>{notificationPermission==='granted'?'Review alerts enabled':'Enable review alerts'}</button>}</section>
            {health.streaming_model==='moss'&&<section className="panel"><h2>Live model readiness</h2><p>Prepare the selected models before a phone demonstration to avoid loading them during the action.</p><button disabled={warming||sourceBusy} onClick={()=>void prepareLiveModels()}>{warming?'Preparing models…':'Prepare live models'}</button>{warmNotice&&<p role="status">{warmNotice}</p>}</section>}<StorageStatus/><div className="controls-grid">{[['Originals preserved','Untouched uploads and hashed incident windows remain separate from generated review copies.'],['People stay anonymous','Only temporary person labels are used. Identity and demographic inference are excluded.'],['Human review required','Confirm, dismiss or request more context. A response illustration does not change a real review decision.'],['Retention and recall',`Incident media expires after ${health.retention_hours||24} hours unless kept for recall. Reviewer decisions remain auditable.`],['Transparent processing','Model errors, fixture results, enhanced media, and research provenance remain visible.'],['Secure phone capture','Pairing links expire after one hour. Camera location is optional; audio stays off.']].map(([title,copy])=><section className="panel" key={title}><Lock size={18}/><h3>{title}</h3><p>{copy}</p></section>)}</div>
          </>:
          tab==='incidents'?<><div className="page-heading"><div><span className="eyebrow">Evidence & review</span><h1>Every moment, in context.</h1><p>Preserved video, reviewed labels, and a clear record of human decisions.</p></div><button onClick={pairPhone}><Smartphone size={16}/>Pair phone</button></div><IncidentQueue incidents={incidents} onOpen={openIncident} uploading={uploading} onChange={applyIncident} removedIncident={removedIncident} onDismissUndo={()=>setRemovedIncident(null)}/></>:
          <><div className="page-heading"><div><span className="eyebrow">01 / Real-time video analysis</span><h1>A second set of eyes.</h1><p>Watch the scene. See the observations. Review the original context.</p></div><div className="page-actions"><label className="button"><Upload size={15}/>Upload recording<input hidden type="file" accept="video/mp4,video/quicktime,video/webm" disabled={uploading} onChange={e=>{const f=e.target.files?.[0];if(f)void upload(f);e.target.value=''}}/></label><button className="primary" onClick={pairPhone}><Smartphone size={16}/>Pair phone camera</button></div></div>
            <div className="live-status-strip"><span><i className={'dot '+(source?.status==='streaming'?'green':'')}/>{source?.status==='streaming'?'Receiving video':source?.status==='processing'?'Finishing analysis':'Ready for a source'}</span><span>{pending.length} awaiting review</span><span>{health.streaming_model==='moss'?'MOSS-VL + visual verification':health.streaming_model||'Checking inference'}</span><span>1 active source capacity</span></div>
            <div id="live-monitor"><LiveMonitor source={source} onPair={pairPhone} onUpload={file=>void upload(file)} onOpen={openIncident}/></div>
            {source?.status==='streaming'&&<div className="live-actions"><button onClick={()=>markMoment(source.id)}><Eye size={15}/>Mark moment for review</button><span className="fine">Preserve the surrounding original context.</span></div>}{bookmarkNotice&&<p className="fine" role="status">{bookmarkNotice}</p>}

            <div className="live-queue-heading"><span>Pending human review</span><button className="text-button" onClick={()=>navigate('incidents')}>All incidents <ArrowRightIcon/></button></div><IncidentQueue incidents={pending.slice(0,4)} compact onOpen={openIncident} onChange={applyIncident}/>
          </>}
      </React.Suspense>
      <footer className="app-footer"><span><Logo size={17}/>Palladium · Campus review & planning</span><span>AI observes. People decide.</span></footer>
    </main>
    {pair&&<div className="modal-backdrop" onClick={()=>setPair('')}><section className="pair-modal" role="dialog" aria-modal="true" aria-labelledby="pair-title" onClick={e=>e.stopPropagation()}><button className="close" onClick={()=>setPair('')} aria-label="Close pairing"><X/></button><Logo size={43}/><h2 id="pair-title">Bring your phone into view.</h2><p>Scan, test phone alerts, allow camera access, then tap Start camera.</p>{warmNotice&&<p className="fine" role="status">{warmNotice}</p>}<div className="qr"><QRCodeSVG value={pair} size={210}/></div><input readOnly aria-label="Phone pairing URL" value={pair}/><button className="primary wide" onClick={()=>navigator.clipboard.writeText(pair)}>Copy pairing link</button><p className="fine">Link expires in one hour. HTTPS is required on your phone. Keep capture open. Allow camera and location on the phone to place live events on the map. Location is optional; turn off Share camera location to stream without it.</p></section></div>}
  </div>;
}
function ArrowRightIcon(){return <ArrowUpRight size={14}/>}
createRoot(document.getElementById('root')!).render(<React.StrictMode>{location.pathname==='/capture'?<Capture/>:<WorkspaceBoundary><WorkspaceStateProvider><App/></WorkspaceStateProvider></WorkspaceBoundary>}</React.StrictMode>);
