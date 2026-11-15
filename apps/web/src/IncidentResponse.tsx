import {useMemo,useState} from 'react';
import {ArrowUpRight,Check,History,MapPin,Radio,RotateCcw,Shield} from 'lucide-react';
import CampusMap from './CampusMap';
import IncidentLocationEditor from './IncidentLocationEditor';
import {useActivityBundle,usePlaces} from './campusData';
import {incidentLocation} from './campusModel.mjs';
import {deriveIncidentCard} from './incidentQueueState.mjs';
import {demoResponders,responseRequested,monthBefore} from './responseState.mjs';
import {localDate,useWorkspaceState,type Point} from './WorkspaceState';
import './incidentResponse.css';
type Data=Record<string,any>;
export default function IncidentResponse({incidents,sources,onOpen,onChange}:{incidents:Data[];sources:Data[];onOpen:(id:string)=>void;onChange:(incident:Data)=>void}){
  const {bundle,error,retry}=useActivityBundle(),{places}=usePlaces();
  const {responseIncident,setResponseIncident,responseRuns,setResponseRuns}=useWorkspaceState();
  const [showHistory,setShowHistory]=useState(true),[threeD,setThreeD]=useState(false),[basemap,setBasemap]=useState<'streets'|'imagery'>('streets'),[picked,setPicked]=useState<Point|null>(null),[picking,setPicking]=useState(false),[reportId,setReportId]=useState<string|null>(null);
  const eligible=incidents.filter(responseRequested);
  const selected=incidents.find(i=>i.id===responseIncident&&!i.record?.queue_deleted?.active&&i.review_status!=='dismissed')||eligible[0];
  const source=sources.find(s=>s.id===selected?.source_id);
  const location=useMemo(()=>selected?incidentLocation(selected,source?.kind):null,[selected,source?.kind]);
  const responders=useMemo(()=>bundle&&location?demoResponders(bundle,location.point):[],[bundle,location]);
  const phase=selected?responseRuns[selected.id]:undefined;
  const active=!!phase&&!!selected&&!!location&&responders.length===4&&responseRequested(selected);
  const end=localDate(),start=monthBefore(end);
  const history=useMemo(()=>bundle?.history?.points.filter((r:any[])=>r[1]>=start&&r[1]<=end)||[],[bundle,start,end]);
  const report=history.find((r:any[])=>r[0]===reportId);
  const focus=useMemo(()=>{
    if(!location)return null;
    if(!active)return {point:location.point,key:Date.now(),zoom:16};
    const points=[location.point,...responders.map(r=>r.point)];
    const bounds:[Point,Point]=[[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1]))],[Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))]];
    return {point:location.point,key:Date.now(),bounds};
  },[location,responders,active]);
  function choose(id:string){setResponseIncident(id);setPicked(null);setPicking(false);setReportId(null)}
  function simulate(){if(!selected||!location||responders.length!==4||!responseRequested(selected))return;setResponseRuns(old=>({...old,[selected.id]:'notified'}))}
  return <div className="incident-response-workspace"><div className="page-heading"><div><span className="eyebrow">02 / Incident response</span><h1>From review to response.</h1><p>Locate the event. See recent history. Illustrate nearby help.</p></div><span className="response-demo-notice"><Radio size={14}/>Demo response — no officers contacted.</span></div>
    <div className="campus-toolbar"><button className={showHistory?'selected':''} aria-pressed={showHistory} onClick={()=>setShowHistory(v=>!v)}><History size={14}/>Past month{showHistory&&bundle?` · ${history.length.toLocaleString()} reports`:''}</button><div className="map-options"><div className="segmented" aria-label="Response map perspective"><button className={!threeD?'selected':''} onClick={()=>setThreeD(false)}>2D</button><button className={threeD?'selected':''} onClick={()=>setThreeD(true)}>3D</button></div><select aria-label="Response map background" value={basemap} onChange={e=>setBasemap(e.target.value as typeof basemap)}><option value="streets">Dark campus</option><option value="imagery">Satellite / aerial</option></select></div></div>
    <div className="incident-response-grid"><aside className="response-sidebar">
      <section className="panel response-queue"><span className="eyebrow">Reviewer-flagged events</span><h2>{eligible.length} awaiting response review</h2>{!eligible.length&&<p>Open a saved video and choose “Needs review” to add it here. The original remains in the review queue.</p>}
        {eligible.map(i=><button key={i.id} className={selected?.id===i.id?'selected':''} onClick={()=>choose(i.id)}><i className="response-red-dot"/><span>{deriveIncidentCard(i).title}<small>{incidentLocation(i,sources.find(s=>s.id===i.source_id)?.kind)?.label||'Location needed'}</small></span><ArrowUpRight size={14}/></button>)}
      </section>
      {selected&&<section className="panel response-selected"><div className="section-heading"><span className="eyebrow">Selected event</span><button className="text-button" onClick={()=>onOpen(selected.id)}>Review video <ArrowUpRight size={13}/></button></div><h3>{deriveIncidentCard(selected).title}</h3><IncidentLocationEditor key={selected.id} incident={selected} sourceKind={source?.kind} onChange={onChange} pickedPoint={picked} onPickMode={()=>setPicking(true)}/>
        {picking&&<p className="fine" role="status">Select the approximate recording area on the map, then save it.</p>}
        {!location&&<p className="response-location-needed"><MapPin size={15}/>Set a recording area or share phone GPS before simulating a response.</p>}
        {!responseRequested(selected)&&<p className="fine">Classify this video as “Needs review” in video review to request a response illustration.</p>}
        {active?<div className="response-notification-status" role="status"><div className="response-broadcast-icon"><Radio size={24}/></div><strong>{phase==='on_scene'?'Demo responders marked on scene':`${responders.length} nearby demo responders notified`}</strong><p>Simulated notifications only. No messages sent to ambassadors or DPS.</p></div>:<button className="primary response-simulate" disabled={!location||responders.length!==4||!responseRequested(selected)} onClick={simulate}><Radio size={15}/>Simulate notifying 4 nearby responders</button>}
        {bundle&&location&&responders.length!==4&&<p className="fine">Four illustrative positions are unavailable in this area. No response has been simulated.</p>}
        {active&&<><ol className="response-unit-list">{responders.map((unit,i)=><li key={unit.id}><b>{i+1}</b><span>{unit.role} · demo<small>Illustrative position · {unit.distance} m away in a straight line</small></span><span>{phase==='on_scene'?'Demo on scene':'Demo notified'}</span></li>)}</ol><div className="response-actions"><button disabled={phase==='on_scene'} onClick={()=>setResponseRuns(old=>({...old,[selected.id]:'on_scene'}))}><Check size={13}/>Mark demo on scene</button><button onClick={()=>setResponseRuns(old=>{const next={...old};delete next[selected.id];return next})}><RotateCcw size={13}/>Reset demo</button></div></>}
      </section>}
      <section className="panel response-history"><Shield size={17}/><h3>Historical context</h3><p>{start} through {end} · occurrence dates from public LAPD reports. Locations are approximate; these reports are separate from reviewed video events.</p>{bundle&&!history.length&&<p>No public reports are available for this period. History is not simulated.</p>}{report&&<div className="history-detail"><h3>{report[4]}</h3><p>{report[1]} · {report[5]||'Time unavailable'}</p>{JSON.parse(report[9]).map((s:Data)=><a key={s.dataset} href={s.url} target="_blank" rel="noreferrer">LAPD source <ArrowUpRight size={12}/></a>)}</div>}</section>
    </aside><div className="response-map-column">{error?<section className="panel unavailable"><h2>Map analysis unavailable</h2><p>{error}</p><button onClick={retry}>Retry map</button></section>:!bundle?<section className="panel unavailable" role="status">Loading saved campus history and map…</section>:<CampusMap bundle={bundle} places={places} threeD={threeD} basemap={basemap} eventLocation={location} focus={focus} history={showHistory?history:[]} responders={active?responders:[]} responseActive={active&&phase==='notified'} picking={picking} onPick={p=>{setPicked(p);setPicking(false)}} onReport={setReportId}/>}
      {active&&<div className="response-map-banner" role="status"><Radio size={19}/><span><strong>{phase==='on_scene'?'Demo responders marked on scene':'4 nearby demo responders notified'}</strong><small>Simulation · no messages sent</small></span></div>}
      <div className="response-map-legend"><span><i className="response-red-dot"/>Selected video event</span><span><i className="history-dot"/>Historical public report</span><span><i className="demo-unit-dot"/>Illustrative responder · demo</span></div>
    </div></div>
  </div>;
}
