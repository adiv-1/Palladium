import {useEffect,useMemo,useRef,useState} from 'react';
import {Map as MapView,NavigationControl,Marker,GeoJSONSource} from 'maplibre-gl';
import {ArrowRight,Clock,Footprints,MapPin,Navigation,Pause,Play,Shield,Sun} from 'lucide-react';
import {makeNetwork,routes,slotFor} from './activityRouting.mjs';
import './activityForecast.css';

type Data=Record<string,any>;
const EMPTY={type:'FeatureCollection' as const,features:[]};
const laParts=(d=new Date())=>Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d).map(p=>[p.type,p.value]));
function laDate(offset=0){const p=laParts();const d=new Date(`${p.year}-${p.month}-${p.day}T12:00:00Z`);d.setUTCDate(d.getUTCDate()+offset);return d.toISOString().slice(0,10)}
const nowHour=()=>Number(laParts().hour);
const hours=(h:number)=>`${String(h).padStart(2,'0')}:00`;
const percent=(p:number)=>`${(p*100).toFixed(1)}%`;
const landmarks=[
  {name:'USC Village · Jefferson / Hoover',point:[-118.2847,34.0258]},
  {name:'Leavey Library',point:[-118.2829,34.0216]},
  {name:'Tommy Trojan',point:[-118.2854,34.0206]},
  {name:'Expo Park / USC station',point:[-118.285,34.0184]},
  {name:'Jefferson / USC station',point:[-118.2781,34.022]},
  {name:'Washington / Vermont',point:[-118.2915,34.0399]},
  {name:'Adams / Normandie',point:[-118.3004,34.0327]},
  {name:'MLK / Vermont · north side',point:[-118.2915,34.0112]},
];

export default function ActivityForecast({bundle}:{bundle:Data}){
  const [date,setDate]=useState(laDate()),[hour,setHour]=useState(nowHour()),[family,setFamily]=useState('person');
  const [minute,setMinute]=useState(Number(laParts().minute)),[basemap,setBasemap]=useState('streets'),[surveys,setSurveys]=useState(false);
  const [staff,setStaff]=useState(3),[showPosts,setShowPosts]=useState(false);
  const [selected,setSelected]=useState<number|null>(null),[animate,setAnimate]=useState(!matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [start,setStart]=useState<number[]|null>(null),[end,setEnd]=useState<number[]|null>(null),[picking,setPicking]=useState<'start'|'end'|null>(null);
  const [detour,setDetour]=useState(.3),[result,setResult]=useState<Data|null>(null),[routeError,setRouteError]=useState(''),[mapError,setMapError]=useState(''),[ready,setReady]=useState(false);
  const host=useRef<HTMLDivElement>(null),map=useRef<MapView|null>(null),pickRef=useRef(picking),markers=useRef<Marker[]>([]);
  const postMarkers=useRef<Marker[]>([]);
  pickRef.current=picking;
  const network=useMemo(()=>makeNetwork(bundle.graph),[bundle]);
  const validDate=!!date&&date>=laDate()&&date<=laDate(7);
  const slot=slotFor(validDate?date:laDate(),hour),values=bundle.weekly[family][slot];
  const familyIndex=family==='person'?0:1,threshold=bundle.density_thresholds[family];
  const posts=bundle.staffing?.scenarios[family][slot].slice(0,staff)||[];
  const windowHour=Math.floor(hour/3)*3,departure=`${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`;
  const cells=useMemo(()=>bundle.cells.map((c:Data,i:number)=>{
    const v=values[i],recent=bundle.model.recent_support[familyIndex][i];
    return {type:'Feature',geometry:c.geometry,properties:{cell:i,id:c.id,mean:v[0],probability:v[1],lower:v[2],upper:v[3],density:v[4],
      level:v[4]>=threshold.high?'elevated':v[4]>=threshold.medium?'moderate':'lower',recent,support:bundle.model.support[familyIndex][i],evidence:recent<5?'Sparse':recent<20?'Limited':'Stronger'}};
  }),[bundle,values,familyIndex,threshold]);
  const streetData=useMemo(()=>({type:'FeatureCollection',features:bundle.streets.features.map((f:Data)=>({...f,properties:{...f.properties,...cells[f.properties.cell].properties}}))}),[bundle,cells]);
  const current=selected===null?null:cells[selected]?.properties;
  const elevated=cells.filter((f:Data)=>f.properties.level==='elevated');
  const ranked=[...cells].sort((a:Data,b:Data)=>b.properties.density-a.properties.density).slice(0,5);

  useEffect(()=>{
    if(!host.current)return;
    let view:MapView;
    try{view=new MapView({container:host.current,center:[-118.284,34.025],zoom:13.5,minZoom:11,maxZoom:18,attributionControl:{compact:true},
      style:{version:8,sources:{base:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,maxzoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>'}},layers:[{id:'base',type:'raster',source:'base',paint:{'raster-saturation':-.85,'raster-opacity':.65}}]}})}catch{setMapError('Map rendering is unavailable. Use the area list and route summary below.');return}
    map.current=view;view.addControl(new NavigationControl({showCompass:false}),'top-right');
    view.on('load',()=>{
      view.addSource('imagery',{type:'raster',tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],tileSize:256,maxzoom:19,
        attribution:'Imagery: Esri, Vantor, Earthstar Geographics, and the GIS User Community · <a href="https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9" target="_blank" rel="noreferrer">World Imagery</a>'});
      view.addLayer({id:'imagery-layer',type:'raster',source:'imagery',layout:{visibility:'none'},paint:{'raster-saturation':-.3,'raster-brightness-max':.8}});
      view.addSource('zone',{type:'geojson',data:bundle.boundary});
      view.addLayer({id:'zone-fill',type:'fill',source:'zone',paint:{'fill-color':'#258b7c','fill-opacity':.025}});
      view.addSource('cells',{type:'geojson',data:EMPTY});
      view.addLayer({id:'areas',type:'fill',source:'cells',paint:{'fill-color':['match',['get','level'],'elevated','#df8438','moderate','#e6b77d','#78968c'],'fill-opacity':['match',['get','level'],'elevated',.12,'moderate',.05,.015]}});
      view.addLayer({id:'sparse',type:'line',source:'cells',filter:['<',['get','recent'],5],paint:{'line-color':'#7c8987','line-opacity':.25,'line-width':.8,'line-dasharray':[2,4]}});
      view.addSource('corridors',{type:'geojson',data:EMPTY});
      view.addLayer({id:'street-glow',type:'line',source:'corridors',filter:['==',['get','level'],'elevated'],paint:{'line-color':'#ed8f3b','line-width':11,'line-opacity':.14,'line-blur':3}});
      view.addLayer({id:'street-lines',type:'line',source:'corridors',paint:{'line-color':['match',['get','level'],'elevated','#dc7b29','moderate','#c69b64','#7d9990'],'line-width':['match',['get','level'],'elevated',3,'moderate',1.5,.65],'line-opacity':['match',['get','level'],'lower',.25,.8]}});
      view.addLayer({id:'zone-line',type:'line',source:'zone',paint:{'line-color':'#277d70','line-width':2,'line-dasharray':[3,2]}});
      view.addSource('selection',{type:'geojson',data:EMPTY});
      view.addLayer({id:'selected-area',type:'line',source:'selection',paint:{'line-color':'#324b52','line-width':2}});
      view.addSource('staff-coverage',{type:'geojson',data:EMPTY});
      view.addLayer({id:'staff-coverage-fill',type:'fill',source:'staff-coverage',paint:{'fill-color':'#277c96','fill-opacity':.12}});
      view.addSource('survey-sites',{type:'geojson',data:{type:'FeatureCollection',features:bundle.pedestrian.observations.map((o:Data)=>({type:'Feature',geometry:{type:'Point',coordinates:o.coordinates},properties:{name:o.name,total:o.pedestrians_sampled}}))}});
      view.addLayer({id:'survey-circles',type:'circle',source:'survey-sites',layout:{visibility:'none'},paint:{'circle-radius':9,'circle-color':'#377e9e','circle-stroke-color':'#ffffff','circle-stroke-width':3}});
      view.addSource('routes',{type:'geojson',data:EMPTY});
      view.addLayer({id:'route-casing',type:'line',source:'routes',paint:{'line-color':'#ffffff','line-width':9}});
      view.addLayer({id:'route-lines',type:'line',source:'routes',paint:{'line-color':['match',['get','kind'],'alternative','#147f72','#526b89'],'line-width':5,'line-opacity':.95}});
      view.on('click',e=>{
        if(pickRef.current){const point=[e.lngLat.lng,e.lngLat.lat];if(pickRef.current==='start')setStart(point);else setEnd(point);setPicking(null);return}
        const hits=view.queryRenderedFeatures(e.point,{layers:['street-lines','areas']});if(hits[0])setSelected(Number(hits[0].properties.cell));
      });
      view.on('mouseenter','street-lines',()=>view.getCanvas().style.cursor='pointer');view.on('mouseleave','street-lines',()=>view.getCanvas().style.cursor='');
      const ring=bundle.boundary.geometry.coordinates[0];
      view.fitBounds([[Math.min(...ring.map((p:number[])=>p[0])),Math.min(...ring.map((p:number[])=>p[1]))],[Math.max(...ring.map((p:number[])=>p[0])),Math.max(...ring.map((p:number[])=>p[1]))]],{padding:30,duration:0});
      setReady(true);
    });
    view.on('error',e=>{if(/WebGL|Worker failed/.test(e.error?.message||''))setMapError('Map rendering is unavailable. Use the area list and route summary below.')});
    return()=>{setReady(false);view.remove();map.current=null};
  },[bundle]);
  useEffect(()=>{const view=map.current;if(!ready||!view)return;view.setLayoutProperty('base','visibility',basemap==='streets'?'visible':'none');view.setLayoutProperty('imagery-layer','visibility',basemap==='imagery'?'visible':'none')},[ready,basemap]);
  useEffect(()=>{const view=map.current;if(ready&&view)view.setLayoutProperty('survey-circles','visibility',surveys?'visible':'none')},[ready,surveys]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    postMarkers.current.forEach(m=>m.remove());postMarkers.current=[];
    if(showPosts)posts.forEach((p:Data,i:number)=>{const el=document.createElement('button');el.className='forecast-post-marker';el.textContent=String(i+1);el.title=`Planning post ${i+1}: near ${p.name}`;el.addEventListener('click',()=>setSelected(p.cell));postMarkers.current.push(new Marker({element:el}).setLngLat(p.coordinates).addTo(view))});
    const covered=showPosts?posts.at(-1)?.covered_cells||[]:[];
    (view.getSource('staff-coverage') as GeoJSONSource).setData({type:'FeatureCollection',features:covered.map((i:number)=>cells[i])});
    return()=>{postMarkers.current.forEach(m=>m.remove())};
  },[ready,showPosts,staff,slot,family,bundle,cells]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    (view.getSource('cells') as GeoJSONSource).setData({type:'FeatureCollection',features:cells});
    (view.getSource('corridors') as GeoJSONSource).setData(streetData as any);
  },[ready,cells,streetData]);
  useEffect(()=>{const view=map.current;if(ready&&view)(view.getSource('selection') as GeoJSONSource).setData(selected===null?EMPTY:{type:'FeatureCollection',features:[cells[selected]]})},[ready,selected,cells]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;let frame=0;
    if(!animate){view.setPaintProperty('street-glow','line-opacity',.14);return}
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    const tick=(time:number)=>{if(!reduced.matches)view.setPaintProperty('street-glow','line-opacity',.1+.12*(1+Math.sin(time/1000))/2);frame=requestAnimationFrame(tick)};
    frame=requestAnimationFrame(tick);return()=>cancelAnimationFrame(frame);
  },[animate,ready]);
  useEffect(()=>{
    setRouteError('');setResult(null);
    if(!start||!end||!validDate)return;
    try{setResult(routes(network,bundle,start,end,date,hour+minute/60,family,detour))}catch(e){setRouteError((e as Error).message)}
  },[start,end,date,hour,minute,family,detour,network,bundle,validDate]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    markers.current.forEach(m=>m.remove());markers.current=[];
    for(const [point,label] of [[start,'A'],[end,'B']] as const)if(point){const el=document.createElement('div');el.className='forecast-pin';el.textContent=label;markers.current.push(new Marker({element:el}).setLngLat(point as [number,number]).addTo(view))}
    const features=result?[{type:'Feature',properties:{kind:'direct'},geometry:{type:'LineString',coordinates:result.direct.coordinates}},...(result.changed?[{type:'Feature',properties:{kind:'alternative'},geometry:{type:'LineString',coordinates:result.alternative.coordinates}}]:[])]:[];
    (view.getSource('routes') as GeoJSONSource).setData({type:'FeatureCollection',features} as any);
  },[ready,start,end,result]);
  const areaNames=(cell:number)=>[...new Set(bundle.streets.features.filter((s:Data)=>s.properties.cell===cell).map((s:Data)=>s.properties.name))].filter(n=>!String(n).startsWith('Unnamed')).slice(0,2).join(' / ')||`Area ${bundle.cells[cell].id}`;
  const validation=bundle.evaluation.candidates.find((c:Data)=>c.config.name===bundle.evaluation.selected);

  return <div className="forecast-workspace">
    <div className="forecast-intro"><div><span className="eyebrow">THE FULL UPC LYFT ZONE · HISTORICAL DATA, FUTURE WINDOWS</span><h1>A little foresight.<br/>A more informed way home.</h1><p>Explore where reports have concentrated at similar times. Compare walking paths before you leave.</p></div><div className="forecast-loaded"><span className="dot green"/>Analysis loaded<small>Time changes and routing run on this device.</small></div></div>
    <div className="forecast-stats"><div><MapPin size={18}/><strong>{(bundle.cells.reduce((n:number,c:Data)=>n+c.area_m2,0)/1e6).toFixed(1)} km²</strong><span>Full published zone</span></div><div><Clock size={18}/><strong>{hours(windowHour)}–{hours(windowHour+3)}</strong><span>Los Angeles · three-hour outlook</span></div><div><Sun size={18}/><strong>{elevated.length} / {cells.length} areas</strong><span>Elevated vs fixed weekly reference</span></div></div>
    <div className="forecast-controls panel"><div><label>Departure date<input aria-label="Forecast departure date" type="date" min={laDate()} max={laDate(7)} value={date} onChange={e=>setDate(e.target.value)}/></label><div className="forecast-presets"><button onClick={()=>{setDate(laDate());setHour(nowHour());setMinute(Number(laParts().minute))}}>Now</button><button onClick={()=>{setDate(laDate());setHour(18);setMinute(0)}}>Tonight</button><button onClick={()=>setDate(laDate(1))}>Tomorrow</button></div></div><label>Report family<select aria-label="Forecast report family" value={family} onChange={e=>setFamily(e.target.value)}>{Object.entries(bundle.families).map(([key,label])=><option key={key} value={key}>{String(label)}</option>)}</select></label><div className="forecast-hour"><label>Departure time · Los Angeles<input aria-label="Exact walking departure time" type="time" value={departure} onChange={e=>{if(e.target.value){const [h,m]=e.target.value.split(':').map(Number);setHour(h);setMinute(m)}}}/></label><input aria-label="Three-hour forecast window" id="forecast-hour" type="range" min={0} max={21} step={3} value={windowHour} onChange={e=>{setHour(+e.target.value);setMinute(0)}}/><div><span>00:00</span><span>12:00</span><span>21:00</span></div></div></div>
    {!validDate&&<p className="error">Choose a date from today through seven days ahead.</p>}
    <div className="forecast-snapshot"><Shield size={14}/><span>Reports through <strong>{bundle.data_through}</strong> · mature training through {bundle.model.training_end}. This is a seasonal forecast, not live incident detection or a personal safety probability.</span></div>
    <div className="forecast-layout"><div className="forecast-map-column"><div className="forecast-map-wrap"><div ref={host} className="forecast-map" aria-label="Forecast report activity and walking routes across the full UPC Lyft zone"/>
      <div className="forecast-map-label"><i/>Published UPC Lyft zone · approximate</div><div className="forecast-map-switches"><button className={basemap==='streets'?'selected':''} onClick={()=>setBasemap('streets')}>Streets</button><button className={basemap==='imagery'?'selected':''} onClick={()=>setBasemap('imagery')}>Satellite / aerial</button><button className={surveys?'selected':''} onClick={()=>setSurveys(!surveys)}>Survey sites {surveys?'on':'off'}</button></div>{picking&&<div className="forecast-pick-notice">Choose {picking==='start'?'your start':'your destination'} on a mapped walking path <button onClick={()=>setPicking(null)}>Cancel</button></div>}
      <div className="forecast-map-legend"><span><i className="forecast-orange"/>Elevated report activity</span><span><i className="forecast-dashed"/>Sparse direct history</span><button onClick={()=>setAnimate(!animate)}>{animate?<Pause size={12}/>:<Play size={12}/>}Pulse {animate?'on':'off'}</button></div>
    </div>{basemap==='imagery'&&<p className="fine">Esri satellite/aerial mosaic · collection dates vary. This background does not change with departure time and is not a pedestrian-count observation. Basemap tiles load online; all analysis stays preloaded.</p>}{mapError&&<p className="error">{mapError}</p>}
    <section className="forecast-staffing panel"><div className="forecast-section-heading"><Shield size={19}/><h2>Ambassador coverage planner</h2><button onClick={()=>setShowPosts(!showPosts)}>{showPosts?'Hide planning posts':'Show planning posts'}</button></div><p>Where could a limited team cover more modeled report activity? Each extra post adds coverage within a {bundle.staffing?.radius_m||400} m walk, accounting for overlap.</p><div className="forecast-staff-controls"><label>Ambassadors available <strong>{staff}</strong><input aria-label="Ambassadors available" type="range" min={1} max={8} value={staff} onChange={e=>setStaff(+e.target.value)}/></label><span>{date} · {hours(windowHour)}–{hours(windowHour+3)}<br/>{bundle.families[family]}</span></div><ol className="forecast-post-list">{posts.map((p:Data,i:number)=><li key={i}><button onClick={()=>{setShowPosts(true);setSelected(p.cell);map.current?.easeTo({center:p.coordinates,zoom:15})}}><b>{i+1}</b><span>Near {p.name}<small>+{p.incremental_expected_reports.toFixed(3)} expected report activity covered · {p.covers.length} representative areas</small></span><ArrowRight size={14}/></button></li>)}</ol><p className="fine">{bundle.staffing?.notice} Rankings update with the date/time window and staff budget; dates sharing a weekday/window share the same seasonal scenario.</p></section>
    <section className="forecast-routes panel"><div className="forecast-section-heading"><Navigation size={19}/><h2>Choose your way home</h2><span>Walking · {departure} departure</span></div><p>Compare a direct walk with a lower-report-activity alternative. More foot traffic has not been assumed to mean safer.</p>
      <div className="forecast-route-inputs">{(['start','end'] as const).map((key,n)=><div key={key}><label>{n?'B · Destination':'A · Start'}<select aria-label={n?'Walking destination':'Walking start'} value="" onChange={e=>{const point=landmarks[+e.target.value]?.point;if(point)(n?setEnd:setStart)(point)}}><option value="">{(n?end:start)?'Point selected · choose another':'Choose a landmark'}</option>{landmarks.map((l,i)=><option key={l.name} value={i}>{l.name}</option>)}</select></label><button className={picking===key?'selected':''} onClick={()=>setPicking(key)}><MapPin size={13}/>Set on map</button><small>{(n?end:start)?.map(v=>v.toFixed(5)).join(', ')||'Landmark or map point'}</small></div>)}<label>Extra walking allowed<select aria-label="Walking detour budget" value={detour} onChange={e=>setDetour(+e.target.value)}><option value={.15}>Up to 15%</option><option value={.3}>Up to 30%</option><option value={.5}>Up to 50%</option></select></label></div>
      {routeError&&<p className="error" role="alert">{routeError}</p>}{result&&<><div className="forecast-route-results">{[{route:result.direct,label:'Direct walking route',kind:'direct'},...(result.changed?[{route:result.alternative,label:'Lower report-activity alternative',kind:'alternative'}]:[])].map(({route,label,kind})=><article className={kind} key={kind}><span className="route-swatch"/><div><strong>{label}</strong><h3>{route.minutes.toFixed(0)} min <small>· {(route.meters/1000).toFixed(2)} km</small></h3><p>{kind==='alternative'?`${Math.round((1-route.activity/result.direct.activity)*100)}% lower modeled report-activity index along the path`:'Shortest connected path at an assumed 80 m/min walking pace.'}</p><small>{Math.round(route.sparseFraction*100)}% of walking time crosses areas with sparse direct history.</small><details><summary>Streets & paths</summary><p>{route.streets.join(' → ')}</p></details></div></article>)}</div>{!result.changed&&<p className="fine">No lower-activity candidate was found within this detour budget. The direct route is shown.</p>}<p className="fine">A / B snap {Math.round(result.startSnapMeters)} / {Math.round(result.endSnapMeters)} m to the connected network. Routes begin/end at those network points; the off-network walk is not assessed. Gate hours, lighting, live closures and crossing conditions are unverified. Candidate routes are evaluated at estimated arrival times; global time-dependent optimality is not claimed.</p></>}
    </section></div>
    <div className="forecast-sidebar"><section className="panel forecast-area-detail"><span className="eyebrow">{current?'AREA OUTLOOK':'EXPLORE THE PATTERN'}</span><h2>{current?areaNames(selected!):'Orange means look closer.'}</h2><p>{current?'Nearby grouped reports expected in the selected three-hour window.':'Street color comes from nearby area estimates. Select a street or area to see its forecast and supporting history.'}</p>
      {current?<><div className="forecast-number">{current.mean.toFixed(3)}<span>expected reports in this area</span></div><div className="forecast-probability"><strong>{percent(current.probability)}</strong><span>Modeled chance of at least one report<br/>in this area/window—not personal harm.</span></div><dl><dt>80% predictive count interval</dt><dd>{current.lower}–{current.upper} reports</dd><dt>Direct report-family history</dt><dd>{current.support} mature reports · {current.recent} in latest training year</dd><dt>Evidence support</dt><dd>{current.evidence} · nearby areas also inform the model</dd></dl><p className="fine">Rare events often have a count interval of 0–0. The probability above retains the small chance of a report. Rounded locations do not establish an exact street.</p></>:<div className="forecast-quiet"><MapPin size={34}/><p>Lower activity is not a safety guarantee. Dashed cells have fewer than five recent training reports.</p></div>}
      <h3>Higher modeled activity</h3><div className="forecast-ranked">{ranked.map((f:Data)=>{const i=Number(f.properties.cell);return <button key={i} onClick={()=>{setSelected(i);map.current?.easeTo({center:bundle.cells[i].center,zoom:15})}}><span>{areaNames(i)}<small>{f.properties.evidence} history · {f.properties.density.toFixed(2)} reports/km²/3h</small></span><ArrowRight size={14}/></button>})}</div></section>
    <section className="panel forecast-footfall"><Footprints size={21}/><h3>Pedestrian evidence</h3><p>{bundle.pedestrian.observations.length} survey corridors matched inside the zone.</p>{bundle.pedestrian.observations.map((o:Data)=><div className="forecast-survey-row" key={o.name}><strong>{o.name}</strong><span>{o.pedestrians_sampled.toLocaleString()} sampled pedestrians · {o.year}</span><small>{o.weekday_period_total} weekday / {o.weekend_period_total} weekend periods</small><button onClick={()=>{setSurveys(true);map.current?.easeTo({center:o.coordinates,zoom:16})}}>Locate survey</button></div>)}<p className="fine">Survey-period totals are not daily or hourly flow. Markers approximate corridors, not exact counter positions. This is not an exhaustive inventory of local counters.</p><span className="forecast-evidence-badge">Footfall excluded from routing</span><a href="https://data.lacity.org/d/hiwe-xzrt" target="_blank" rel="noreferrer">Inspect LADOT source <ArrowRight size={12}/></a></section></div></div>
    <details className="panel forecast-method"><summary>Model, validation & full-zone coverage</summary><div className="forecast-method-grid"><div><h3>Precomputed seasonal outlook</h3><p>{bundle.evaluation.selected}. Recency half-life: {bundle.model.half_life_days} days. Training: {bundle.model.training_start}–{bundle.model.training_end}. A complete weekday × three-hour lookup is loaded for every area. Dates with the same weekday share an outlook; this first model has no day-specific event inputs.</p><p>Spatially pooled weighted Poisson rates, with approximate Gamma-Poisson count distributions. No identity, financial-only, warrant-only or administrative-only labels are used as hotspot signals. Person-directed reports may include indoor and relationship contexts.</p></div><div><h3>Retrospective evidence</h3><p>Selected using predictive log scores on three earlier seven-day periods. Final untouched periods: {validation?.final_test_folds?.map((f:Data)=>f.cutoff).join(', ')}. Small samples and a revised historical snapshot limit these results.</p>{validation?.final_test_folds?.map((f:Data)=><p key={f.cutoff} className="fine">{f.cutoff}: {f.report_family_occurrences} report-family occurrences · {percent(f.top_20_percent_area_capture)} captured in a fixed 20% hotspot area. Model complexity is not evidence of better routing.</p>)}<p className="fine">The seasonal model slightly improved count log scores, while static density captured more reports in these final hotspot tests. Personal safety, route effectiveness, and nominal interval calibration are not established.</p></div><div><h3>Every part of the zone stays visible</h3><p>{bundle.cells.length} clipped areas · {bundle.graph.coverage.cells_with_connections} have mapped walking connections. {bundle.graph.coverage.components} network components; routing uses the largest connected component. The boundary is a published-map approximation, not a ride eligibility guarantee.</p><p className="fine">Snapshot {bundle.model_id} · OSM attribution and ODbL apply. Data stays loaded until refresh; changing time or requesting a route makes no analysis API call.</p></div></div></details>
  </div>;
}
