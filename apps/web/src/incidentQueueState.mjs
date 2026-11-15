// Display provenance explicitly; this helper never changes incident records.
export function deriveIncidentCard(incident) {
  const record=incident.record||{},report=record.clip_review?.report;
  const manual=record.trigger_origin==='reviewer';
  const actionTitle=manual?'Requested clip review':record.action==='object_taking'?'Object-taking flagged':record.action==='physical_contact'?'Physical contact flagged':'Possible event flagged';
  const job=incident.processing_summary?.review_brief;
  const status=typeof job==='string'?job:job?.status;
  const waiting=status==='queued'||status==='running';
  let reviewLabel='Secondary review not available',reviewTone='muted';
  if(waiting)reviewLabel=status==='running'?'Secondary review running':'Secondary review queued';
  else if(report){
    reviewLabel=report.alert_support==='not_supported'?'Secondary review disagrees':report.alert_support==='uncertain'?'Secondary review uncertain':'Secondary review complete';
    reviewTone=report.alert_support==='not_supported'||report.alert_support==='uncertain'?'attention':'complete';
  } else if(status==='failed'){reviewLabel='Secondary review failed';reviewTone='attention'}
  else if(status==='not_requested')reviewLabel='Secondary review not requested';
  const context=record.reviewer_context||record.uploader_context;
  const contextText=typeof context==='string'?context:context?.text;
  const provenance=typeof context==='object'?context?.provenance:'';
  const contextLabel=record.uploader_context||/uploader/i.test(provenance||'')?'Uploader context':'Reviewer context';
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
    title:manual&&report?.takeaway&&!waiting?report.takeaway:actionTitle,
    actionTitle,manual,reviewLabel,reviewTone,
    reviewTakeaway:typeof report?.takeaway==='string'?report.takeaway:null,
    reviewTakeawayLabel:waiting?'Previous secondary review':'Secondary review',
    initialObservation:record.observable_events?.[0]?.observation||null,
    contextText:typeof contextText==='string'?contextText:null,contextLabel,
    held:record.retention_hold?.active===true,locationLabel,
    reviewStatus:({pending:'Awaiting review',confirmed:'Flagged for follow-up',dismissed:'Dismissed',needs_review:'Needs more context'})[incident.review_status]||'Awaiting review',
    duration:Number.isFinite(duration)&&duration>=0?duration:null,
    lighting:typeof record.lighting_condition==='string'?record.lighting_condition.replaceAll('_',' '):null,
    mediaUnavailable:record.expired===true||record.media_evicted===true&&!record.files?.thumbnail,
  };
}
