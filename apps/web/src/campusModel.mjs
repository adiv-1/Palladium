import {makeNetwork, routes, slotFor} from './activityRouting.mjs';

// Presentation never chooses a different model for 2D, 3D, routing or staffing.
export function campusModel(bundle, state) {
  const slot = slotFor(state.date, state.hour);
  const values = bundle.weekly[state.family][slot];
  const family = state.family === 'person' ? 0 : 1;
  const threshold = bundle.density_thresholds[state.family];
  const cells = bundle.cells.map((cell, i) => {
    const row = values[i], recent = bundle.model.recent_support[family][i];
    return {type:'Feature', geometry:cell.geometry, properties:{cell:i, id:cell.id, mean:row[0], probability:row[1], lower:row[2], upper:row[3], density:row[4], recent,
      support:bundle.model.support[family][i], evidence:recent<5?'Sparse':recent<20?'Limited':'Stronger',
      level:row[4]>=threshold.high?'elevated':row[4]>=threshold.medium?'moderate':'lower'}};
  });
  const posts = (bundle.staffing?.scenarios[state.family][slot] || []).slice(0, state.staff);
  const total = values.reduce((n, row) => n+row[0], 0);
  const covered = posts.at(-1)?.cumulative_expected_reports || 0;
  return {slot, values, cells, posts, covered, coverage:total>0?Math.min(1,covered/total):0,
    streets:{type:'FeatureCollection',features:bundle.streets.features.map(f=>({...f,properties:{...f.properties,...cells[f.properties.cell].properties}}))}};
}

export function incidentLocation(incident, sourceKind) {
  const supplied=incident.record?.reviewer_location;
  if(supplied?.active===true&&supplied.provenance==='reviewer_supplied'&&typeof supplied.label==='string'&&supplied.label.trim()&&[supplied.latitude,supplied.longitude].every(v=>typeof v==='number'&&Number.isFinite(v))&&Math.abs(supplied.latitude)<=90&&Math.abs(supplied.longitude)<=180){
    return {point:[supplied.longitude,supplied.latitude],accuracy:null,measuredAt:null,provenance:'reviewer_supplied',label:supplied.label.trim()};
  }
  if (sourceKind === 'upload' || sourceKind === 'rtsp') return null;
  const fix = incident.record?.geolocation;
  if (!fix || fix.status !== 'active' || fix.provenance !== 'browser_permission') return null;
  if (![fix.latitude,fix.longitude,fix.accuracy_m,fix.measured_at].every(v=>typeof v==='number'&&Number.isFinite(v))) return null;
  if (Math.abs(fix.latitude)>90 || Math.abs(fix.longitude)>180 || fix.accuracy_m<0) return null;
  return {point:[fix.longitude,fix.latitude], accuracy:fix.accuracy_m, measuredAt:fix.measured_at, provenance:'browser_permission',label:'Camera-reported position'};
}

export function responseRoute(bundle, staging, target) {
  return routes(makeNetwork(bundle.graph),bundle,staging,target,bundle.model.cutoff,0,'person',0).direct;
}

export function demoTransition(state, action) {
  if(action.type==='reset') return {staging:null,phase:'idle'};
  if(action.type==='staging') return {staging:action.place,phase:'idle'};
  if(action.type==='start' && state.staging && action.path?.length>1) return {...state,phase:'en_route',path:action.path,startedAt:action.now};
  if(action.type==='arrived' && ['en_route','awaiting'].includes(state.phase)) return {...state,phase:'on_scene'};
  if(action.type==='await' && state.phase==='en_route') return {...state,phase:'awaiting'};
  return state;
}

export function pointAlongPath(path, fraction) {
  if(!path?.length) return null;
  const meters = (a,b)=>Math.hypot((a[0]-b[0])*92300,(a[1]-b[1])*111320);
  const lengths=path.slice(1).map((p,i)=>meters(path[i],p));
  let remaining=lengths.reduce((a,b)=>a+b,0)*Math.max(0,Math.min(1,fraction));
  for(let i=0;i<lengths.length;i++) {
    if(remaining<=lengths[i]) {const t=lengths[i]?remaining/lengths[i]:0;return path[i].map((v,j)=>v+(path[i+1][j]-v)*t);}
    remaining-=lengths[i];
  }
  return path.at(-1);
}
