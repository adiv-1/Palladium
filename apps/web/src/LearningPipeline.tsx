import {useEffect,useRef,useState} from 'react';
import {ArrowRight,Check,ChevronRight,CloudUpload,Database,FileCheck,GitBranch,Layers,Lock,Pause,Play,RotateCcw,ShieldCheck,SkipForward,Workflow} from 'lucide-react';
import {LEARNING_DEMO_DURATION,learningDemoStages,learningDemoOutput,learningPreviewEligible} from './learningDemoState.mjs';
import {deriveIncidentCard} from './incidentQueueState.mjs';
import {useWorkspaceState,type LearningDemo} from './WorkspaceState';
import './learningPipeline.css';
type Data=Record<string,any>;
const icons=[GitBranch,FileCheck,Database,ShieldCheck,Layers,Workflow,FileCheck,CloudUpload,Lock];
const emptyDemo:LearningDemo={elapsed:0,playing:false,selectedRole:null};
export default function LearningPipeline({incidents,onOpen,onRepository}:{incidents:Data[];onOpen:(id:string)=>void;onRepository:()=>void}){
  const {learningIncident,setLearningIncident}=useWorkspaceState();
  const confirmed=incidents.filter(learningPreviewEligible);
  const incident=confirmed.find(i=>i.id===learningIncident)||confirmed[0];
  return <div className="learning-pipeline-page"><div className="page-heading"><div><span className="eyebrow">04 / Learning pipeline</span><h1>From confirmed event to candidate.</h1><p>Specialized roles prepare reviewed data, train a Qwen LoRA adapter, evaluate it, and version the result.</p></div><button onClick={onRepository}>Learning repository <ArrowRight size={14}/></button></div>
    <div className="learning-preview-notice"><Workflow size={19}/><span><strong>Workflow simulation</strong><small>Agent roles map to existing review and training workers. This playback launches no training, evaluation or Hugging Face upload.</small></span></div>
    {!incident?<div className="pipeline-overview"><section className="panel pipeline-empty"><ShieldCheck size={30}/><h2>Start with a confirmed event.</h2><p>Review original footage and choose “Confirm event · Learning pipeline.” Confirmation opens this walkthrough; training reuse permission remains a separate decision.</p><button onClick={onRepository}>Inspect the real learning repository <ArrowRight size={14}/></button></section><section className="panel"><AgentDiagram stages={learningDemoStages(0)} selected=""/></section></div>:<><div className="pipeline-event-choice"><label>Confirmed event<select aria-label="Confirmed pipeline event" value={incident.id} onChange={e=>setLearningIncident(e.target.value)}>{confirmed.map(i=><option key={i.id} value={i.id}>{deriveIncidentCard(i).title} · {i.id.slice(0,8)}</option>)}</select></label><span><Check size={14}/>Human confirmation recorded</span><button className="text-button" onClick={()=>onOpen(incident.id)}>Review original <ArrowRight size={13}/></button></div><PipelinePlayback key={incident.id} incident={incident}/></>}
  </div>;
}
function PipelinePlayback({incident}:{incident:Data}){
  const {learningDemos,setLearningDemos}=useWorkspaceState();
  const run=learningDemos[incident.id]||emptyDemo;
  const [view,setView]=useState<'timeline'|'diagram'>('timeline');
  const canvas=useRef<HTMLDivElement>(null),timeline=useRef<HTMLElement>(null);
  const patch=(values:Partial<LearningDemo>)=>setLearningDemos(old=>({...old,[incident.id]:{...(old[incident.id]||emptyDemo),...values}}));
  useEffect(()=>{
    if(!run.playing)return;
    let last=performance.now();
    const timer=window.setInterval(()=>{
      const now=performance.now(),delta=Math.min(500,now-last);last=now;
      if(document.hidden)return;
      const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
      setLearningDemos(old=>{
        const previous=old[incident.id]||emptyDemo;if(!previous.playing)return old;
        const elapsed=reduced?LEARNING_DEMO_DURATION:Math.min(LEARNING_DEMO_DURATION,previous.elapsed+delta);
        return {...old,[incident.id]:{...previous,elapsed,playing:elapsed<LEARNING_DEMO_DURATION}};
      });
    },200);
    return()=>window.clearInterval(timer);
  },[incident.id,run.playing,setLearningDemos]);
  const stages=learningDemoStages(run.elapsed);
  const finished=run.elapsed>=LEARNING_DEMO_DURATION;
  const active=stages.find(s=>s.status==='demo_running')||stages.find(s=>s.status==='demo_pending')||stages.at(-1)!;
  const selected=stages.find(s=>s.id===run.selectedRole)||active;
  const complete=stages.filter(s=>s.status==='demo_complete').length;
  const select=(id:string)=>patch({selectedRole:id});
  const output=learningDemoOutput(incident,selected.id);
  useEffect(()=>{if(run.playing&&!document.hidden)canvas.current?.scrollIntoView({block:'start',behavior:'auto'})},[run.playing]);
  useEffect(()=>{
    if(view!=='timeline'||run.selectedRole||!run.playing)return;
    const panel=timeline.current,row=panel?.querySelector(`[data-role="${active.id}"]`);if(!panel||!row)return;
    const a=panel.getBoundingClientRect(),b=row.getBoundingClientRect();
    const delta=b.bottom>a.bottom-12?b.bottom-a.bottom+12:b.top<a.top+12?b.top-a.top-12:0;
    if(delta)panel.scrollBy({top:delta,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  },[active.id,view,run.selectedRole,run.playing]);
  function play(){patch({elapsed:finished?0:run.elapsed,playing:!run.playing,selectedRole:null})}
  function next(){patch({elapsed:Math.min(LEARNING_DEMO_DURATION,active.end+1),selectedRole:null})}
  return <div className="pipeline-playback" ref={canvas}>
    <div className="pipeline-playback-controls"><div className="segmented" aria-label="Pipeline presentation"><button className={view==='timeline'?'selected':''} aria-pressed={view==='timeline'} onClick={()=>setView('timeline')}>Agent timeline</button><button className={view==='diagram'?'selected':''} aria-pressed={view==='diagram'} onClick={()=>setView('diagram')}>Architecture diagram</button></div><div><button className="primary" onClick={play}>{run.playing?<Pause size={13}/>:<Play size={13}/>} {run.playing?'Pause demo':finished?'Replay demo':'Play workflow demo'}</button><button disabled={finished} onClick={next}><SkipForward size={13}/>Next stage</button><button aria-label="Reset workflow demo" onClick={()=>patch({...emptyDemo})}><RotateCcw size={14}/></button></div></div>
    <div className="pipeline-demo-progress"><progress max={LEARNING_DEMO_DURATION} value={Math.min(run.elapsed,LEARNING_DEMO_DURATION)} aria-label="Workflow demonstration progress"/><span>{complete} / {stages.length} illustrated stages · {finished?'Awaiting human release approval':run.playing?'Demo playing':'Demo paused'}</span></div>
    <div className="learning-agent-layout"><section ref={timeline} className="panel learning-agent-stages" aria-label="Learning agent stages">
      {view==='diagram'?<AgentDiagram stages={stages} selected={selected.id} onSelect={select}/>:<ol className="agent-timeline">{stages.map((stage,i)=>{const Icon=icons[i];return <li key={stage.id} data-role={stage.id} data-status={stage.status}><button aria-pressed={selected.id===stage.id} className={selected.id===stage.id?'selected':''} onClick={()=>select(stage.id)}><span className="agent-stage-icon">{stage.status==='demo_complete'?<Check size={19}/>:<Icon size={19}/>}</span><span className="agent-stage-copy"><span className="agent-stage-title">{stage.name}<small>{stage.worker}</small></span><span className="agent-stage-description">{stage.description}</span><span className="agent-stage-status">{stage.status==='demo_complete'?'Demo complete':stage.id===active.id&&run.playing?'Demo running':'Planned stage'}{stage.id===active.id&&run.playing&&<progress max={1} value={stage.progress} aria-label={`${stage.name} demo progress`}/>}</span></span><ChevronRight size={14}/></button></li>})}</ol>}
    </section><aside className="agent-output-column"><section className="panel agent-output-panel"><header><div><i className="agent-demo-dot"/><h2>Agent output preview</h2></div><span>Simulated · browser session only</span></header><div className="agent-output-role"><span>{selected.worker}</span><h3>{selected.name}</h3><p>{selected.description}</p>{run.selectedRole&&<button className="text-button" onClick={()=>patch({selectedRole:null})}>Follow active stage <ArrowRight size={12}/></button>}</div><pre aria-label="Simulated agent output">{JSON.stringify(output,null,2)}</pre></section>
      <section className="panel hf-artifact-summary"><CloudUpload size={21}/><div><h3>What goes to Hugging Face?</h3><p>A private, versioned adapter bundle: weights, configuration, aggregate evaluation, manifest and model card. Original footage and training frames are excluded.</p><button className="text-button" onClick={()=>select('registry')}>Inspect the planned bundle <ArrowRight size={12}/></button></div></section>
      <p className="pipeline-model-policy"><Lock size={13}/>Qwen LoRA adapts the verifier. MOSS is not retrained. Publishing never activates weights automatically.</p>
    </aside></div>
  </div>;
}
function AgentDiagram({stages,selected,onSelect}:{stages:ReturnType<typeof learningDemoStages>;selected:string;onSelect?:(id:string)=>void}){
  const position=(i:number)=>{const row=Math.floor(i/3),column=row%2?2-i%3:i%3;return {x:18+column*232,y:18+row*136}};
  return <div className="agent-diagram-wrap"><div className="agent-diagram-heading"><Workflow size={17}/><span>Multi-stage architecture · demo</span></div><svg viewBox="0 0 710 420" role="group" aria-label="Reviewed learning architecture diagram"><defs><marker id="learning-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="currentColor"/></marker></defs>
    {stages.slice(0,-1).map((stage,i)=>{const a=position(i),b=position(i+1),same=a.y===b.y,forward=b.x>a.x;const d=same?`M ${forward?a.x+210:a.x} ${a.y+51} L ${forward?b.x:b.x+210} ${b.y+51}`:`M ${a.x+105} ${a.y+102} L ${b.x+105} ${b.y}`;return <path key={stage.id} d={d} className={'agent-flow-edge '+(stage.status==='demo_complete'?'passed':'')} markerEnd="url(#learning-arrow)"/>})}
    {stages.map((stage,i)=>{const p=position(i);return <g key={stage.id} role={onSelect?'button':'group'} tabIndex={onSelect?0:undefined} aria-label={`${onSelect?'Inspect ':''}${stage.name}`} aria-pressed={onSelect?selected===stage.id:undefined} data-status={stage.status} className={`agent-flow-node ${selected===stage.id?'selected':''}`} onClick={()=>onSelect?.(stage.id)} onKeyDown={e=>{if(onSelect&&(e.key==='Enter'||e.key===' ')){e.preventDefault();onSelect(stage.id)}}}><rect x={p.x} y={p.y} width="210" height="102" rx="11"/><text x={p.x+15} y={p.y+24} className="flow-stage-number">{String(i+1).padStart(2,'0')} / {stage.status==='demo_complete'?'Demo complete':'Planned role'}</text><text x={p.x+15} y={p.y+50}>{stage.name}</text><text x={p.x+15} y={p.y+77} className="flow-worker-label">{stage.worker}</text></g>})}
  </svg><p className="fine">Each role maps to an existing worker or human gate. Connections illustrate handoffs; no autonomous agents are executing in this preview.</p></div>;
}
