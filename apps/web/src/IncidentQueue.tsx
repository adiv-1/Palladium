import {ArrowUpRight,Check,Clock,Lock,MapPin} from 'lucide-react';
import {deriveIncidentCard} from './incidentQueueState.mjs';
import './incidentQueue.css';

type Incident=Record<string,any>;
export default function IncidentQueue({incidents,onOpen,uploading=false}:{incidents:Incident[];onOpen:(id:string)=>void;uploading?:boolean}){
  return <section className="incident-queue-v2" aria-label="Incident queue">
    <div className="section-heading"><div><h2>Incident queue <span>{incidents.length}</span></h2><p>Initial observations, separate AI reviews, and your saved context.</p></div><span className="fine">{uploading?'Uploading video…':'Newest first'}</span></div>
    {!incidents.length?<div className="empty-queue"><Check size={26}/><h3>No incidents to review.</h3><p>Saved moments appear here with their original footage and review status.</p></div>:<div className="incident-queue-grid">{incidents.map(incident=>{
      const card=deriveIncidentCard(incident);
      const date=new Date(incident.created_at);
      const recorded=Number.isNaN(date.getTime())?'Time unavailable':date.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
      return <button type="button" className="incident-queue-card" key={incident.id} onClick={()=>onOpen(incident.id)}>
        <div className="incident-queue-image">{card.mediaUnavailable?<span>Retained thumbnail unavailable</span>:<img src={`/api/incidents/${encodeURIComponent(incident.id)}/media/thumbnail`} alt="" loading="lazy"/>}
          <div className="incident-queue-image-badges">{incident.simulated&&<span className="incident-queue-badge simulated">Simulated</span>}{card.held&&<span className="incident-queue-badge held"><Lock size={11}/>Saved for recall</span>}</div>
        </div>
        <div className="incident-queue-copy">
          <div className="incident-queue-meta"><span><Clock size={12}/>{recorded}</span><span>{card.reviewStatus}</span></div>
          <h3>{card.title}</h3>
          {card.contextText&&<div className="incident-queue-context"><strong>{card.contextLabel}</strong><p>{card.contextText}</p></div>}
          <div className={`incident-queue-secondary ${card.reviewTone}`}><span className="incident-queue-review-dot"/>{card.reviewLabel}</div>
          {card.reviewTakeaway&&!(card.manual&&card.title===card.reviewTakeaway)&&<div className="incident-queue-observation"><strong>{card.reviewTakeawayLabel}</strong><p>{card.reviewTakeaway}</p></div>}
          {card.initialObservation&&<div className="incident-queue-observation initial"><strong>{card.manual?'Original review request':'Initial observation'}</strong><p>{card.initialObservation}</p></div>}
          <div className="incident-queue-details">{card.duration!==null&&<span>{card.duration.toFixed(1)}s preserved</span>}{card.lighting&&<span>{card.lighting}</span>}{card.locationLabel&&<span className="incident-queue-location"><MapPin size={12}/>{card.locationLabel}</span>}</div>
          <div className="incident-queue-open">Review original & findings <ArrowUpRight size={16}/></div>
        </div>
      </button>;
    })}</div>}
  </section>;
}
