import {useEffect,useRef,useState} from 'react';
import {Camera,Smartphone,Upload,Radio,Eye,Sun,Clock,Activity,MessageSquare,AlertTriangle} from 'lucide-react';
type Data=Record<string,any>;
export const INCIDENT_LABELS:Record<string,string>={person_down:'Person down',altercation:'Physical altercation',rack_activity:'Activity at bike rack'};
export const incidentLabel=(r:Data)=>r?.incident_type?INCIDENT_LABELS[r.incident_type]||r.incident_type:null;
function CostMeter({c}:{c:Data}){
  const rows:[string,string][]=[
    ['Tier 0 detector + tracker',`${c.tier0_ms_per_frame} ms / frame`],
    ['Tier 1 pose (flagged only)',`${c.tier1_pose_calls} calls${c.tier1_pose_calls?` · ${c.tier1_ms_per_call} ms`:''}`],
    ['Tier 2 VLM checks',`${c.tier2_vlm_calls}${c.tier2_vlm_calls_per_hour!=null?` · ${c.tier2_vlm_calls_per_hour}/h`:''}${c.tier2_dismissed?` · ${c.tier2_dismissed} dismissed`:''}`],
    ['Rule candidates',Object.entries(c.candidates||{}).map(([k,v])=>`${INCIDENT_LABELS[k]||k} ${v}`).join(' · ')||'None'],
    ['Cameras this device could run',c.camera_equivalents_this_device!=null?`≈${c.camera_equivalents_this_device} (${c.device}, measured)`:'Measuring'],
  ];
  return <div className="observation-box cost-meter"><span className="eyebrow">PER-CAMERA COST · MEASURED</span>{rows.map(([label,value])=><div className="telemetry-row" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>;
}
const socketUrl=(path:string)=>`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}${path}`;
const clock=(n:number)=>`${Math.floor(Math.max(0,n)/60)}:${String(Math.floor(Math.max(0,n)%60)).padStart(2,'0')}`;
async function decode(data:Blob):Promise<ImageBitmap|HTMLImageElement>{
  if('createImageBitmap' in window)return createImageBitmap(data);
  return new Promise((resolve,reject)=>{const img=new Image(),url=URL.createObjectURL(data);img.onload=()=>{URL.revokeObjectURL(url);resolve(img)};img.onerror=()=>{URL.revokeObjectURL(url);reject(Error('Unable to display frame'))};img.src=url});
}
function LiveCanvas({source}:{source:Data}){
  const canvas=useRef<HTMLCanvasElement>(null);
  const [status,setStatus]=useState('Connecting preview…'),[metrics,setMetrics]=useState<Data>({fps:0});
  useEffect(()=>{
    let stopped=false,ws:WebSocket,retry=0,meta:Data|null=null,stage=0,chain=Promise.resolve();
    let count=0,since=performance.now(),lastUi=0;setMetrics({fps:0});
    function connect(){
      if(stopped)return;ws=new WebSocket(socketUrl(`/ws/preview?source_id=${encodeURIComponent(source.id)}`));ws.binaryType='blob';
      ws.onopen=()=>setStatus('Live · newest frame');
      ws.onmessage=event=>{chain=chain.then(async()=>{
        if(stopped)return;
        if(typeof event.data==='string'){const m=JSON.parse(event.data);if(m.type==='status'){setStatus(m.status==='completed'?'Stream ended':'Waiting for camera…');return}meta=m;stage=0;return}
        if(!meta)return;const frame=meta;
        if(stage===0){const image=await decode(event.data);if(!stopped&&canvas.current){
          const target=canvas.current;if(target.width!==image.width||target.height!==image.height){target.width=image.width;target.height=image.height}
          target.getContext('2d')!.drawImage(image,0,0);
        }if('close' in image)image.close();count++;stage=1;}
        else stage=2; // Consume optional experimental MOSS localization frame without mislabeling it.
        if(!frame.objects_frame_included||stage===2){if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'displayed',index:frame.index}));meta=null}
        const now=performance.now();if(now-lastUi>400){setMetrics({fps:count*1000/Math.max(1,now-since),rtt:frame.previous_roundtrip_ms});lastUi=now;if(now-since>2000){count=0;since=now}}
      }).catch(()=>{if(!stopped){setStatus('Reconnecting preview…');ws.close()}})};
      ws.onclose=()=>{if(!stopped){setStatus('Reconnecting preview…');retry=window.setTimeout(connect,700)}};ws.onerror=()=>setStatus('Preview connection interrupted');
    }
    connect();return()=>{stopped=true;clearTimeout(retry);ws?.close()};
  },[source.id]);
  const alert=source.telemetry?.review_alert,flagged=alert&&source.telemetry.timestamp-(alert.received_stream_time??alert.timestamp)<35;
  return <div className={'live-video-wrap '+(flagged?'flagged':'')}><div className="preview-heading"><span><i className={'dot '+(flagged?'red':'green')}/>{flagged?'Possible incident · review required':status}</span><span>{metrics.fps.toFixed(1)} display FPS {metrics.rtt!=null&&`· ${Math.round(metrics.rtt)} ms display round trip`}</span></div><div className="canvas-stage"><canvas ref={canvas} aria-label="Live original video"/><span className="source-caption">ORIGINAL LIVE FEED · {source.name}</span></div></div>
}
function ObservationFeed({source,onOpen}:{source?:Data,onOpen:(id:string)=>void}){
  const list=useRef<HTMLDivElement>(null),stick=useRef(true);
  const notes:Data[]=source?.telemetry?.moss_chat||[],alert=source?.telemetry?.review_alert;
  const alerts:Data[]=source?.telemetry?.review_alerts?.length?source.telemetry.review_alerts:alert?[alert]:[];
  const gate=source?.telemetry?.alert_gate,observation=source?.telemetry?.last_observation;
  const eventName=(action:string)=>action==='physical_contact'?'Physical contact flagged':action==='object_taking'?'Object-taking action flagged':'Possible incident';
  const live=source?.status==='streaming';
  useEffect(()=>{if(stick.current&&list.current)list.current.scrollTop=list.current.scrollHeight},[notes.length,alert?.id,alert?.status,source?.telemetry?.alert_gate?.reason]);
  return <section className="observation-feed"><div className="chat-heading"><div><MessageSquare size={18}/><strong>MOSS live observations</strong></div><span className={'pill '+(live?'teal':'')}>{live?'Listening':'Paused'}</span></div><p className="chat-intro">Automatic scene observations while video is running. Received timestamps show when a response arrived, not an exact action time.</p>
    <div ref={list} className="chat-messages" role="log" aria-live="polite" onScroll={e=>{const el=e.currentTarget;stick.current=el.scrollHeight-el.scrollTop-el.clientHeight<70}}>
      {!notes.length&&<div className="chat-empty"><Eye size={28}/><h3>{live?'MOSS is watching.':'Start a video to begin.'}</h3><p>{live?'Observations appear after the model responds. Silence is not a guarantee that nothing happened.':'Connect a phone or upload a clip. Model observations will appear here automatically.'}</p></div>}
      {notes.map(note=><article className="chat-bubble" key={note.id}><div><span className="model-avatar">M</span><strong>MOSS</strong><time>Received · {clock(note.received_stream_time)}</time></div><p>{note.text}</p></article>)}
      {gate?.reason==='awaiting_confirmation'&&<article className="chat-bubble"><strong>Checking a possible event</strong><p>{observation?.observable_events?.[0]?.observation}</p><small>A second original-frame set is being checked before creating an incident.</small></article>}{alerts.map(alert=><article className="chat-bubble alert-bubble" key={alert.id}><div><AlertTriangle size={17}/><strong>{eventName(alert.action)}</strong><time>{clock(alert.timestamp)}</time></div><p>{alert.message}</p><small>Model score {Math.round((alert.confidence??0)*100)} / threshold {Math.round((alert.threshold??0)*100)} · two original-frame sets. Scores are uncalibrated; human review is required.</small><div className="recording-state">{alert.status==='saved'?'Clip saved automatically':alert.status==='failed'?'Recording failed — check source storage':'Saving surrounding video…'}</div>{alert.status==='saving_context'&&<p className="fine">Preserving the buffered lead-up and the configured post-event context.</p>}{alert.incident_id&&<button className="primary" onClick={()=>onOpen(alert.incident_id)}>Open incident review</button>}{alert.error&&<p className="error">{alert.error}</p>}</article>)}
    </div><div className="chat-footer"><i className={'dot '+(live?'green':'')}/>{live?'Video receipt continues while MOSS processes.':'Observations remain attached to this source.'}</div></section>
}
export default function LiveMonitor({source,onPair,onUpload,onOpen}:{source?:Data,onPair:()=>void,onUpload:(file:File)=>void,onOpen:(id:string)=>void}){
  const live=source?.status==='streaming';
  return <section className="monitor panel"><div className="panel-title"><Radio size={18}/><h3>Cameras</h3><span className={'pill '+(live?'teal':'')}>{source?.status||'No source connected'}</span></div><div className="live-chat-layout">
    {live?<LiveCanvas source={source!}/>:<div className="empty-camera live-empty"><div className="camera-ring"><Camera size={34}/></div><h3>A view starts here.</h3><p>Connect a phone or upload a video to start the live view and MOSS observation feed.</p><div><button className="primary" onClick={onPair}><Smartphone size={16}/>Pair phone camera</button><label className="button"><Upload size={16}/>Upload video<input hidden type="file" accept="video/mp4,video/quicktime,video/webm" onChange={e=>e.target.files?.[0]&&onUpload(e.target.files[0])}/></label></div></div>}
    <ObservationFeed source={source} onOpen={onOpen}/></div>
    <div className="live-telemetry">{[[Sun,'Lighting',source?.telemetry?.lighting?.state?.replaceAll('_',' ')||'Waiting'],[Clock,'Preserved context',`${(source?.telemetry?.buffer_seconds||0).toFixed(1)}s buffered`],[Activity,'Event verification',source?.telemetry?.safety_state==='NORMAL'?'No event flagged':source?.telemetry?.safety_state?.replaceAll('_',' ')||'Awaiting video'],[Eye,'Analysis status',source?.telemetry?.model_status||'Not started']].map(([Icon,label,value]:any)=><div key={label}><Icon size={15}/><span>{label}</span><strong>{value}</strong></div>)}</div>
    {source?.telemetry?.cascade&&<CostMeter c={source.telemetry.cascade}/>}
    {source?.telemetry?.error&&<p className="error">{source.telemetry.error}</p>}
  </section>
}
