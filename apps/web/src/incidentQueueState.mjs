// Display provenance explicitly; this helper never changes incident records.
export function deriveIncidentCard(incident) {
  const record=incident.record||{};
  const manual=record.trigger_origin==='reviewer';
  const initialObservation=typeof record.observable_events?.[0]?.observation==='string'?record.observable_events[0].observation:null;
  const label=record.reviewed_label;
  const reviewed=!!label&&typeof label.title==='string'&&label.title.trim().length>0;
  const tags=reviewed&&Array.isArray(label.tags)?label.tags.filter(t=>typeof t==='string'&&t.trim()).map(t=>t.trim()):[];
  const gps=record.geolocation;
  let locationLabel=null;
  if(gps){
    const fixed=gps.status==='active'&&typeof gps.latitude==='number'&&Number.isFinite(gps.latitude)&&Math.abs(gps.latitude)<=90&&typeof gps.longitude==='number'&&Number.isFinite(gps.longitude)&&Math.abs(gps.longitude)<=180&&Number.isFinite(gps.accuracy_m)&&gps.accuracy_m>=0;
    if(fixed)locationLabel=Number.isFinite(gps.accuracy_m)?`Phone location · ±${Math.round(gps.accuracy_m)} m`:'Phone location saved';
    else if(gps.status==='denied')locationLabel='Location permission denied';
    else locationLabel=gps.status==='active'?'Phone location pending':'Phone location unavailable';
  }
  const duration=Number(incident.end_time)-Number(incident.start_time);
  return {
    title:reviewed?label.title.trim():'Saved video for review',
    description:reviewed&&typeof label.description==='string'?label.description:'Review the preserved original footage and its context.',
    reviewed,tags,deleted:record.queue_deleted?.active===true,
    manual,initialObservation,
    held:record.retention_hold?.active===true,locationLabel,
    reviewStatus:({pending:'Awaiting review',confirmed:'Flagged for follow-up',dismissed:'Dismissed',needs_review:'Needs more context'})[incident.review_status]||'Awaiting review',
    duration:Number.isFinite(duration)&&duration>=0?duration:null,
    lighting:typeof record.lighting_condition==='string'?record.lighting_condition.replaceAll('_',' '):null,
    mediaUnavailable:record.expired===true||record.media_evicted===true&&!record.files?.thumbnail,
  };
}

export function updateIncidentQueue(incidents,incident){
  const remaining=incidents.filter(item=>item.id!==incident.id);
  return incident.record?.queue_deleted?.active===true?remaining:[incident,...remaining].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
}
