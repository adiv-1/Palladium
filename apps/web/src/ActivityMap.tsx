import React,{useEffect,useRef,useState} from 'react';
import {Map as MapView,NavigationControl,GeoJSONSource,setWorkerUrl} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import {CalendarDays,MapPin,ExternalLink,X} from 'lucide-react';
setWorkerUrl(workerUrl);

type Row=[string,string,number,number,string];
type RecordData=Record<string,any>;
const today=new Date().toLocaleDateString('en-CA');
const yearsAgo=(n:number)=>{const d=new Date();d.setFullYear(d.getFullYear()-n);return d.toLocaleDateString('en-CA')};
const day=(s:string)=>Date.parse(s+'T00:00:00Z')/86400000;
async function read(path:string,signal?:AbortSignal){const r=await fetch('/api/activity'+path,{signal});if(!r.ok){const e=await r.json();throw Error(e.detail||'Unable to load public records')}return r.json()}

export default function ActivityMap(){
  const host=useRef<HTMLDivElement>(null),map=useRef<MapView|null>(null);
  const [meta,setMeta]=useState<RecordData|null>(null),[rows,setRows]=useState<Row[]>([]),[start,setStart]=useState(yearsAgo(10)),[end,setEnd]=useState(today),[category,setCategory]=useState('All categories');
  const [ready,setReady]=useState(false),[busy,setBusy]=useState(true),[error,setError]=useState(''),[mapError,setMapError]=useState(''),[selected,setSelected]=useState(''),[detail,setDetail]=useState<RecordData|null>(null),[detailError,setDetailError]=useState('');
  const filtered=category==='All categories'?rows:rows.filter(r=>r[4]===category);
  useEffect(()=>{const abort=new AbortController();read('/meta',abort.signal).then(setMeta).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>abort.abort()},[]);
  useEffect(()=>{
    if(!meta||!start||!end||start>end)return;
    const abort=new AbortController();setBusy(true);setError('');setSelected('');
    const timer=setTimeout(()=>read(`/points?start=${start}&end=${end}`,abort.signal).then(d=>setRows(d.points)).catch(e=>{if(e.name!=='AbortError')setError(e.message)}).finally(()=>{if(!abort.signal.aborted)setBusy(false)}),300);
    return()=>{clearTimeout(timer);abort.abort()};
  },[meta,start,end]);
  useEffect(()=>{
    if(!meta||!host.current)return;
    let view:MapView;
    try{
      view=new MapView({container:host.current,center:[-118.284,34.024],zoom:13.6,minZoom:11,maxZoom:18,
        style:{version:8,sources:{streets:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,maxzoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>'}},layers:[{id:'streets',type:'raster',source:'streets',paint:{'raster-saturation':-.75,'raster-opacity':.72}}]},
        attributionControl:{compact:false}});
      map.current=view;view.addControl(new NavigationControl({showCompass:false}),'top-right');
      view.on('load',()=>{
        view.addSource('lyft',{type:'geojson',data:meta.boundary});
        view.addLayer({id:'lyft-fill',type:'fill',source:'lyft',paint:{'fill-color':'#258b7c','fill-opacity':.025}});
        view.addLayer({id:'lyft-line',type:'line',source:'lyft',paint:{'line-color':'#258b7c','line-width':2,'line-dasharray':[3,2]}});
        view.addSource('reports',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
        view.addLayer({id:'reports',type:'circle',source:'reports',layout:{'circle-sort-key':['get','day']},paint:{'circle-radius':['interpolate',['linear'],['zoom'],12,2.5,16,4,18,5],
          'circle-color':'#e6a868','circle-opacity':1,'circle-stroke-color':'#fff8ed','circle-stroke-width':.6}});
        view.on('mouseenter','reports',()=>view.getCanvas().style.cursor='pointer');view.on('mouseleave','reports',()=>view.getCanvas().style.cursor='');
        view.on('click','reports',e=>{const f=e.features?.[0];if(f)setSelected(String(f.properties.id))});
        view.fitBounds([[-118.303,34.008],[-118.266,34.041]],{padding:25,duration:0});setReady(true);
      });
      view.on('error',e=>{if(/WebGL|Worker failed/.test(e.error?.message||''))setMapError('The map renderer is unavailable. You can still browse the records below.')});
    }catch{setMapError('The map renderer is unavailable. You can still browse the records below.')}
    return()=>{setReady(false);map.current=null;view?.remove()};
  },[meta]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    const source=view.getSource('reports') as GeoJSONSource;
    source.setData({type:'FeatureCollection',features:filtered.map(r=>({type:'Feature',properties:{id:r[0],date:r[1],day:day(r[1])},geometry:{type:'Point',coordinates:[r[2],r[3]]}}))});
    const a=day(start),b=Math.max(a+1,day(end));
    if(Number.isFinite(a)&&Number.isFinite(b))view.setPaintProperty('reports','circle-color',['interpolate',['linear'],['get','day'],a,'#f5d7af',b,'#b95b16']);
  },[rows,category,ready,start,end]);
  useEffect(()=>{
    setDetail(null);setDetailError('');if(!selected)return;
    const abort=new AbortController();read('/incidents/'+selected,abort.signal).then(setDetail).catch(e=>{if(e.name!=='AbortError')setDetailError(e.message)});return()=>abort.abort();
  },[selected]);
  const point=rows.find(r=>r[0]===selected),nearby=point?filtered.filter(r=>r[2]===point[2]&&r[3]===point[3]):[];
  return <div className="activity-page">
    <div className="activity-intro"><span className="eyebrow">PUBLIC RECORDS · UNIVERSITY PARK</span><h1>Campus activity, in context.</h1><p>Explore reported activity over time. A little context goes further than an alarming headline.</p></div>
    <section className="activity-filters panel" aria-label="Activity date filters"><CalendarDays size={20}/><label>From<input aria-label="Activity start date" type="date" min={meta?.date_min||'2010-01-01'} max={end} value={start} onChange={e=>setStart(e.target.value)}/></label><label>Through<input aria-label="Activity end date" type="date" min={start} max={today} value={end} onChange={e=>setEnd(e.target.value)}/></label><label>Reported category<select aria-label="Activity category" value={category} onChange={e=>setCategory(e.target.value)}><option>All categories</option>{Object.keys(meta?.categories||{}).sort().map(c=><option key={c}>{c}</option>)}</select></label><div className="activity-presets">{[1,5,10].map(n=><button key={n} onClick={()=>{setStart(yearsAgo(n));setEnd(today)}}>{n} year{n>1?'s':''}</button>)}<button onClick={()=>{setStart(meta?.date_min||'2010-01-01');setEnd(today)}}>All history</button></div></section>
    {(!start||!end||start>end)&&<p className="error">Choose a valid date range.</p>}{error&&<p className="error" role="alert">{error}</p>}
    <div className="activity-map-heading"><div><strong>{busy?'Loading records…':`${filtered.length.toLocaleString()} reports`}</strong><span> · occurrence date · approximate locations</span></div><div className="activity-legend"><span>Older</span><i/><span>More recent</span></div></div>
    <div className="activity-map-grid"><div className="activity-map-wrap"><div ref={host} className="activity-map" aria-label="Map of reported activity within the published UPC Lyft area"/>{busy&&<span className="map-loading" role="status">Updating date range…</span>}<div className="map-boundary-key"><i/>Published UPC Lyft area · approximate</div></div>
      <section className="activity-detail panel" aria-label="Selected activity summary" aria-live="polite">
        {selected?<><button className="text-button activity-close" aria-label="Close activity summary" onClick={()=>setSelected('')}><X size={17}/></button><span className="eyebrow">PUBLIC REPORT</span>{!detail?<p>{detailError||'Loading report…'}</p>:<><h2>{detail.category}</h2><p className="activity-date">{new Date(detail.occurred+'T12:00:00').toLocaleDateString(undefined,{year:'numeric',month:'long',day:'numeric'})}</p><p>{detail.summary}</p><dl><dt>Listed occurrence time</dt><dd>{detail.time||'Not supplied'} · Los Angeles local time</dd><dt>Reported on</dt><dd>{detail.reported||'Not supplied'}</dd><dt>Public case number</dt><dd>{detail.case_number}</dd></dl><details><summary>Source classifications</summary>{detail.offenses.map((o:string)=><p className="fine" key={o}>{o}</p>)}</details><p className="fine">{detail.context}</p>{detail.sources.map((s:RecordData)=><a className="activity-source" href={s.url} key={s.dataset} target="_blank" rel="noreferrer">LAPD source dataset <ExternalLink size={12}/></a>)}{nearby.length>1&&<><h4>{nearby.length} reports share this approximate point</h4><select aria-label="Other reports at this location" value={selected} onChange={e=>setSelected(e.target.value)}>{nearby.slice().reverse().map(r=><option key={r[0]} value={r[0]}>{r[1]} · {r[4]} · {r[0].slice(0,4)}</option>)}</select></>}</>}</>:<><div className="activity-pin"><MapPin size={25}/></div><h2>A point is a starting place.</h2><p>Select an orange circle for a short summary, date, and original data source.</p><p className="fine">Darker orange means more recent within your date range. Circle size and color do not indicate severity.</p><div className="activity-context">Reports describe past activity. They cannot tell you whether a person or street is safe.</div></>}
      </section></div>
    {mapError&&<p className="error">{mapError}</p>}
    <details className="activity-record-list"><summary>Browse records as a list ({filtered.length.toLocaleString()})</summary><p className="fine">Most recent 100 matching reports. Narrow the date range to explore others.</p>{filtered.slice(-100).reverse().map(r=><button key={r[0]} onClick={()=>{setSelected(r[0]);map.current?.easeTo({center:[r[2],r[3]],zoom:16})}}>{r[1]} · {r[4]} <MapPin size={12}/></button>)}</details>
    {meta&&<details className="activity-method panel"><summary>About these records & coverage</summary><p>{meta.count.toLocaleString()} grouped reports · {meta.date_min} through {meta.date_max} · snapshot downloaded {meta.downloaded_at.slice(0,10)}.</p>{meta.notes.map((n:string)=><p className="fine" key={n}>{n}</p>)}<p className="fine">No records after {meta.date_max} appear in this snapshot. An empty date range does not establish that no incidents occurred.</p><a href={meta.boundary.properties.source_url} target="_blank" rel="noreferrer">USC Lyft program boundary</a><span> · </span><a href={meta.boundary.properties.map_url} target="_blank" rel="noreferrer">Published USC map</a>{meta.sources.map((s:RecordData)=><p key={s.id}><a href={s.url} target="_blank" rel="noreferrer">{s.name}</a></p>)}</details>}
  </div>
}
