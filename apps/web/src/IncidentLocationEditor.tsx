import {useState} from 'react';
import './incidentLocationEditor.css';
import {MapPin, Pencil, X} from 'lucide-react';
import CameraLocation from './CameraLocation';
import PlaceSearch from './PlaceSearch';
import {usePlaces} from './campusData';
import {incidentLocation} from './campusModel.mjs';
import type {Place,Point} from './WorkspaceState';
type Data=Record<string,any>;
export default function IncidentLocationEditor({incident,sourceKind,onChange,pickedPoint,onPickMode}:{incident:Data;sourceKind?:string;onChange:(incident:Data)=>void;pickedPoint?:Point|null;onPickMode?:()=>void}){
  const [editing,setEditing]=useState(false);
  const location=incidentLocation(incident,sourceKind);
  return <section className="incident-location-editor">
    {location?.provenance==='reviewer_supplied'?<div className="camera-location"><MapPin size={19}/><div><strong>{location.label}</strong><p>Reviewer-supplied recording area · approximate</p><small>Assigned by a reviewer. Original phone GPS, if present, remains in the audit.</small></div></div>:<CameraLocation location={incident.record?.geolocation} title="Recorded camera location"/>}
    <button className="text-button" onClick={()=>setEditing(v=>!v)} aria-expanded={editing}><Pencil size={13}/>{editing?'Close location editor':location?'Adjust recording location':'Set recording location'}</button>
    {editing&&<LocationForm key={incident.id} incident={incident} onChange={result=>{onChange(result);setEditing(false)}} pickedPoint={pickedPoint} onPickMode={onPickMode}/>}
  </section>;
}
function LocationForm({incident,onChange,pickedPoint,onPickMode}:{incident:Data;onChange:(incident:Data)=>void;pickedPoint?:Point|null;onPickMode?:()=>void}){
  const {places,error:placeError}=usePlaces();
  const [place,setPlace]=useState<Place|null>(null),[label,setLabel]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const point=place?.point||pickedPoint;
  async function update(active=true){
    setBusy(true);setError('');
    try{
      const response=await fetch(`/api/incidents/${encodeURIComponent(incident.id)}/location`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(active?{active:true,latitude:point?.[1],longitude:point?.[0],label:label.trim()||place?.name||'Reviewer-selected map point',note:'Reviewer assigned the approximate recording area using the location editor.'}:{active:false,note:'Reviewer cleared the assigned area; original camera GPS is preserved.'})});
      const result=await response.json();if(!response.ok)throw Error(result.detail||'Unable to save the recording location');onChange(result);
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <div className="location-form"><PlaceSearch label="Find recording area" places={places} value={place} onChange={setPlace}/>{placeError&&<p className="fine">{placeError}</p>}
    {onPickMode&&<button onClick={()=>{setPlace(null);onPickMode()}}><MapPin size={13}/>Pick recording area on map</button>}
    {point&&<p className="fine">Selected area: {point[1].toFixed(5)}, {point[0].toFixed(5)}</p>}
    <label>Area label · optional<input value={label} maxLength={200} placeholder={place?.name||'Describe the recording area'} onChange={e=>setLabel(e.target.value)}/></label>
    <p className="fine">This saves an approximate area supplied by you. It does not create a phone GPS fix or change the video’s review decision.</p>
    {error&&<p className="error" role="alert">{error}</p>}<div className="response-actions"><button className="primary" disabled={busy||!point} onClick={()=>void update()}>{busy?'Saving…':'Save recording location'}</button>{incident.record?.reviewer_location?.active&&<button disabled={busy} onClick={()=>void update(false)}><X size={13}/>Clear assigned area</button>}</div>
  </div>;
}
