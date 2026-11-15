import {useMemo,useState} from 'react';
import {MapPin,Radio,RotateCcw} from 'lucide-react';
import CampusMap from './CampusMap';
import PlaceSearch from './PlaceSearch';
import {useActivityBundle,usePlaces} from './campusData';
import {demoTransition,incidentLocation,pointAlongPath,responseRoute} from './campusModel.mjs';
import {useWorkspaceState,type ResponseDemo as Demo,type Place} from './WorkspaceState';
import {useVisibleAnimation} from './useVisibleAnimation';
type Data=Record<string,any>;
export default function ResponseDemo({incident,sourceKind}:{incident:Data;sourceKind?:string}) {
  const location=useMemo(()=>incidentLocation(incident,sourceKind),[incident,sourceKind]);
  // No analysis or map request is made for an incident without a usable location.
  if(!location)return <section className="panel response-unavailable"><MapPin size={18}/><div><h3>Camera location unavailable</h3><p>No saved permissioned camera fix is available for this recording. A response destination has not been inferred.</p></div></section>;
  return <LocatedResponse key={incident.id} id={incident.id} location={location}/>;
}
function LocatedResponse({id,location}:{id:string;location:NonNullable<ReturnType<typeof incidentLocation>>}) {
  const {bundle,error,retry}=useActivityBundle(),{places}=usePlaces(),{demos,setDemos}=useWorkspaceState();
  const [open,setOpen]=useState(true),[threeD,setThreeD]=useState(false),[failure,setFailure]=useState('');
  const demo:Demo=demos[id]||{staging:null,phase:'idle'};
  const action=(value:Data)=>setDemos(old=>({...old,[id]:demoTransition(old[id]||{staging:null,phase:'idle'},value)}));
  const timing=useMemo(()=>{const base=Math.min(1,Math.max(0,(Date.now()-(demo.startedAt||Date.now()))/12000));return {base,duration:Math.max(1,12000*(1-base))};},[demo.startedAt]);
  const progress=useVisibleAnimation(open&&demo.phase==='en_route',timing.duration,()=>action({type:'await'}));
  const unit=demo.path?pointAlongPath(demo.path,demo.phase==='en_route'?timing.base+(1-timing.base)*progress:demo.phase==='idle'?0:1):null;
  const path=useMemo(()=>demo.path?{demo:demo.path}:null,[demo.path]);
  const focus=useMemo(()=>({point:location.point,key:1,zoom:15}),[location.point[0],location.point[1]]);
  const endpoints=useMemo(()=>demo.staging?[{point:demo.staging.point,label:'Demo start'}]:[],[demo.staging]);
  function stage(place:Place|null){setFailure('');action({type:'staging',place});}
  function start() {
    if(!bundle||!demo.staging)return;
    try {const route=responseRoute(bundle,demo.staging.point,location.point);setFailure('');action({type:'start',path:route.coordinates,now:Date.now()});}
    catch(e){setFailure((e as Error).message);}
  }
  const labels={idle:'Choose a demo staging point',en_route:'Illustrative unit en route',awaiting:'Awaiting demo arrival confirmation',on_scene:'On scene · marked by reviewer in demo'};
  return <section className="panel response-demo"><div className="section-heading"><div><span className="eyebrow">Location & response illustration</span><h2>Demo response — no officers contacted.</h2><p>{location.provenance==='reviewer_supplied'?`${location.label} · reviewer-supplied approximate area.`:`Camera-reported position · ±${Math.round(location.accuracy||0)} m. This is not a verified incident location.`}</p></div><button onClick={()=>setOpen(v=>!v)} aria-expanded={open}><MapPin size={14}/>{open?'Hide event map':'Open event map'}</button></div>
    {open&&<>{error?<p className="error">{error}<button onClick={retry}>Retry map analysis</button></p>:!bundle?<p role="status">Loading the saved walking network…</p>:<div className="response-layout"><div className="response-controls"><PlaceSearch label="Demo staging point · illustrative unit" value={demo.staging} places={places} onChange={stage}/><button className="primary" disabled={!demo.staging||demo.phase!=='idle'} onClick={start}><Radio size={14}/>Simulate response</button><p className="response-phase" role="status">{labels[demo.phase]}</p><p className="fine">Animation speed and the unit are illustrative. No officer position, message delivery, or acknowledgement is represented.</p><div className="response-actions"><button disabled={!['en_route','awaiting'].includes(demo.phase)} onClick={()=>action({type:'arrived'})}>Mark on scene</button><button onClick={()=>{action({type:'reset'});setFailure('');}}><RotateCcw size={13}/>Reset demo</button></div>{failure&&<p className="error" role="alert">{failure} The camera pin remains visible; a movement path has not been invented.</p>}<div className="segmented" aria-label="Response map perspective"><button className={!threeD?'selected':''} onClick={()=>setThreeD(false)}>2D</button><button className={threeD?'selected':''} onClick={()=>setThreeD(true)}>3D</button></div></div><CampusMap bundle={bundle} places={places} threeD={threeD} basemap="streets" eventLocation={location} focus={focus} endpoints={endpoints} route={path} unit={unit}/></div>}</>}
  </section>;
}
