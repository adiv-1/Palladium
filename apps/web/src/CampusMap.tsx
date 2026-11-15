import {useEffect, useRef, useState} from 'react';
import {Map as MapView, NavigationControl, GeoJSONSource, Marker, setWorkerUrl} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import './campusMap.css';
import {campusMapStyle} from './mapStyle';
import type {Place, Point} from './WorkspaceState';

setWorkerUrl(workerUrl);
type Data = Record<string,any>;
const empty = () => ({type:'FeatureCollection' as const, features:[]});
export type MapFocus = {point:Point; key:number; zoom?:number; bounds?:[Point,Point]};
type Props = {
  bundle:Data; places:Place[]; threeD:boolean; basemap:'streets'|'imagery'; surveys?:boolean;
  cells?:Data[]; streets?:Data; selected?:number|null; posts?:Data[]; covered?:number[];
  route?:Data|null; endpoints?:{point:Point;label:string}[]; history?:any[][];
  focus?:MapFocus|null; unit?:Point|null; picking?:boolean; eventLocation?:{point:Point;accuracy:number}|null;
  onPick?:(point:Point)=>void; onSelect?:(cell:number)=>void; onReport?:(id:string)=>void; onPost?:(index:number)=>void;
};
export default function CampusMap(props:Props) {
  const host=useRef<HTMLDivElement>(null), map=useRef<MapView|null>(null), latest=useRef(props);
  latest.current=props;
  const [ready,setReady]=useState(false), [error,setError]=useState('');
  useEffect(()=>{
    if(!host.current)return;
    let view:MapView;
    try {view=new MapView({container:host.current,style:campusMapStyle(),center:[-118.284,34.025],zoom:13.5,minZoom:10,maxZoom:19,attributionControl:{compact:true}});}
    catch {setError('Map rendering is unavailable. The analysis and controls below remain available.');return;}
    map.current=view;
    view.addControl(new NavigationControl(),'top-right');
    view.on('load',()=>{
      view.addSource('imagery',{type:'raster',tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],tileSize:256,attribution:'Imagery © Esri, Vantor, Earthstar Geographics and the GIS User Community'});
      view.addLayer({id:'imagery',type:'raster',source:'imagery',layout:{visibility:'none'},paint:{'raster-brightness-max':.8}},'street-names');
      for(const name of ['zone','cells','streets','selection','coverage','surveys','routes','reports','buildings','building-names','unit']) view.addSource(name,{type:'geojson',data:empty()});
      view.addLayer({id:'zone',type:'line',source:'zone',paint:{'line-color':'#8c979f','line-width':1.5,'line-dasharray':[3,3]}});
      view.addLayer({id:'areas',type:'fill',source:'cells',paint:{'fill-color':['match',['get','level'],'elevated','#dc874b','moderate','#b29c77','#738a88'],'fill-opacity':.1}});
      view.addLayer({id:'sparse',type:'line',source:'cells',filter:['<',['get','recent'],5],paint:{'line-color':'#a3a5ab','line-opacity':.3,'line-width':1,'line-dasharray':[2,4]}});
      view.addLayer({id:'coverage',type:'fill',source:'coverage',paint:{'fill-color':'#b7c9d7','fill-opacity':.17}});
      view.addLayer({id:'buildings-3d',type:'fill-extrusion',source:'omt','source-layer':'building',minzoom:14,layout:{visibility:'none'},paint:{'fill-extrusion-color':'#373d47','fill-extrusion-height':['coalesce',['get','render_height'],8],'fill-extrusion-base':['coalesce',['get','render_min_height'],0],'fill-extrusion-opacity':.9}});
      view.addLayer({id:'footprints',type:'line',source:'buildings',minzoom:15,paint:{'line-color':'#6f7783','line-width':.7,'line-opacity':.5}});
      view.addLayer({id:'corridors',type:'line',source:'streets',paint:{'line-color':['match',['get','level'],'elevated','#e9a05e','moderate','#bb9c75','#77908b'],'line-width':['match',['get','level'],'elevated',3,'moderate',1.5,.8],'line-opacity':.85}});
      view.addLayer({id:'selection',type:'line',source:'selection',paint:{'line-color':'#ededed','line-width':2}});
      view.addLayer({id:'routes-casing',type:'line',source:'routes',paint:{'line-color':'#121417','line-width':8}});
      view.addLayer({id:'routes',type:'line',source:'routes',paint:{'line-color':['match',['get','kind'],'alternative','#ffcc00','demo','#b4b9ef','#c0c5ce'],'line-width':4}});
      view.addLayer({id:'reports',type:'circle',source:'reports',paint:{'circle-color':'#efa467','circle-radius':4,'circle-opacity':.75,'circle-stroke-width':.5,'circle-stroke-color':'#fff'}});
      view.addLayer({id:'surveys',type:'circle',source:'surveys',paint:{'circle-color':'#7eafcf','circle-radius':8,'circle-stroke-width':2,'circle-stroke-color':'#ededed'}});
      view.addLayer({id:'building-names',type:'symbol',source:'building-names',minzoom:15,layout:{'text-field':['get','name'],'text-font':['Noto Sans Regular'],'text-size':12,'text-max-width':13,'text-padding':8},paint:{'text-color':'#e4e6eb','text-halo-color':'#16181c','text-halo-width':2}});
      view.addLayer({id:'unit',type:'circle',source:'unit',paint:{'circle-radius':7,'circle-color':'#c4c8ff','circle-stroke-width':2,'circle-stroke-color':'#15171c'}});
      view.on('click',e=>{
        const p:Point=[e.lngLat.lng,e.lngLat.lat];
        if(latest.current.picking){latest.current.onPick?.(p);return;}
        const hits=view.queryRenderedFeatures(e.point,{layers:['reports','corridors','areas']});
        const hit=hits[0]; if(!hit)return;
        if(hit.layer.id==='reports')latest.current.onReport?.(String(hit.properties.id));
        else latest.current.onSelect?.(Number(hit.properties.cell));
      });
      const ring=props.bundle.boundary.geometry.coordinates[0];
      view.fitBounds([[Math.min(...ring.map((p:Point)=>p[0])),Math.min(...ring.map((p:Point)=>p[1]))],[Math.max(...ring.map((p:Point)=>p[0])),Math.max(...ring.map((p:Point)=>p[1]))]],{padding:35,duration:0});
      setReady(true);
    });
    view.on('error',e=>{if(/WebGL|Worker|Failed to fetch|AJAXError/.test(e.error?.message||''))setError('Some basemap tiles could not load. Local analysis remains available.');});
    const resize=new ResizeObserver(()=>view.resize());resize.observe(host.current);
    return()=>{resize.disconnect();view.remove();map.current=null;};
  },[props.bundle]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    const data=(name:string,value:any)=>(view.getSource(name) as GeoJSONSource).setData(value);
    data('zone',props.bundle.boundary);
    data('cells',{type:'FeatureCollection',features:props.cells||[]});
    data('streets',props.streets||empty());
    data('selection',{type:'FeatureCollection',features:props.selected!=null&&props.cells?.[props.selected]?[props.cells[props.selected]]:[]});
    data('coverage',{type:'FeatureCollection',features:(props.covered||[]).map(i=>props.cells?.[i]).filter(Boolean)});
    data('reports',{type:'FeatureCollection',features:(props.history||[]).map(r=>({type:'Feature',properties:{id:r[0]},geometry:{type:'Point',coordinates:[r[2],r[3]]}}))});
    const route=props.route;
    data('routes',{type:'FeatureCollection',features:route?.demo?[{type:'Feature',properties:{kind:'demo'},geometry:{type:'LineString',coordinates:route.demo}}]:route?[{type:'Feature',properties:{kind:'direct'},geometry:{type:'LineString',coordinates:route.direct.coordinates}},...(route.changed?[{type:'Feature',properties:{kind:'alternative'},geometry:{type:'LineString',coordinates:route.alternative.coordinates}}]:[])]:[]});
    data('surveys',{type:'FeatureCollection',features:props.surveys?(props.bundle.pedestrian?.observations||[]).map((o:Data)=>({type:'Feature',properties:{name:o.name},geometry:{type:'Point',coordinates:o.coordinates}})):[]});
    data('buildings',{type:'FeatureCollection',features:props.places.filter(p=>p.footprint?.length).map(p=>({type:'Feature',properties:{name:p.name},geometry:{type:'Polygon',coordinates:[p.footprint]}}))});
    data('building-names',{type:'FeatureCollection',features:props.places.filter(p=>p.footprint?.length).map(p=>({type:'Feature',properties:{name:p.tags?.short_name||p.name},geometry:{type:'Point',coordinates:p.point}}))});
  },[ready,props.bundle,props.cells,props.streets,props.selected,props.covered,props.history,props.route,props.surveys,props.places]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    (view.getSource('unit') as GeoJSONSource).setData({type:'FeatureCollection',features:props.unit?[{type:'Feature',properties:{},geometry:{type:'Point',coordinates:props.unit}}]:[]});
  },[ready,props.unit]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    view.easeTo({pitch:props.threeD?58:0,bearing:props.threeD?-18:0,duration:reduced?0:700});
    view.setLayoutProperty('buildings-3d','visibility',props.threeD?'visible':'none');
    view.setLayoutProperty('imagery','visibility',props.basemap==='imagery'?'visible':'none');
  },[ready,props.threeD,props.basemap]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view||!props.focus)return;
    const duration=matchMedia('(prefers-reduced-motion: reduce)').matches?0:700;
    if(props.focus.bounds)view.fitBounds(props.focus.bounds,{padding:55,maxZoom:17,duration});
    else view.easeTo({center:props.focus.point,zoom:props.focus.zoom||15.5,duration});
  },[ready,props.focus]);
  useEffect(()=>{
    const view=map.current;if(!ready||!view)return;
    const markers:Marker[]=[];
    const marker=(point:Point,label:string,kind:string,click?:()=>void)=>{const el=document.createElement(click?'button':'div');el.className=`map-pin ${kind}`;el.textContent=label;el.setAttribute('aria-label',kind==='post'?`Planning post ${label}`:label);if(click)el.addEventListener('click',click);markers.push(new Marker({element:el}).setLngLat(point).addTo(view));};
    props.endpoints?.forEach(p=>marker(p.point,p.label,'endpoint'));
    props.posts?.forEach((p,i)=>marker(p.coordinates,String(i+1),'post',()=>latest.current.onPost?.(i)));
    if(props.eventLocation)marker(props.eventLocation.point,'Camera','event');
    return()=>markers.forEach(m=>m.remove());
  },[ready,props.endpoints,props.posts,props.eventLocation]);
  return <div className={`campus-map-frame ${props.picking?'picking':''}`}><div ref={host} className="campus-map" aria-label={props.eventLocation?'Camera-reported position and simulated response':'UPC campus activity map'}/>{error&&<p className="map-error" role="status">{error}</p>}<span className="map-scope">{props.eventLocation?`Camera-reported · ±${Math.round(props.eventLocation.accuracy)} m`:'Published UPC Lyft zone · approximate'}</span></div>;
}
