import {useEffect, useId, useMemo, useRef, useState} from 'react';
import './placeSearch.css';
import type {Place} from './WorkspaceState';
export default function PlaceSearch({label, value, places, onChange}: {label: string; value: Place | null; places: Place[]; onChange: (place: Place | null) => void}) {
  const id = useId(), [text, setText] = useState(value?.name || ''), [open, setOpen] = useState(false), [active, setActive] = useState(0);
  const editing = useRef(false);
  useEffect(() => {if(!editing.current) setText(value?.name || ''); editing.current = false;}, [value]);
  const results = useMemo(() => {
    const q = text.trim().toLowerCase();
    if (!q) return places.slice(0, 6);
    return places.filter(p => `${p.name} ${p.aliases || ''}`.toLowerCase().includes(q)).sort((a,b) => Number(!a.name.toLowerCase().startsWith(q))-Number(!b.name.toLowerCase().startsWith(q))).slice(0, 7);
  }, [text, places]);
  const pick = (place: Place) => {onChange(place); setText(place.name); setOpen(false);};
  return <div className="place-search"><label htmlFor={id}>{label}</label><div className="place-input-wrap"><input id={id} value={text} role="combobox" autoComplete="off" placeholder="Search a building or landmark" aria-expanded={open} aria-autocomplete="list" aria-controls={`${id}-list`} aria-activedescendant={open && results[active] ? `${id}-${active}` : undefined}
    onChange={e => {setText(e.target.value); setOpen(true); setActive(0); if(value) {editing.current=true; onChange(null);}}}
    onFocus={() => setOpen(true)} onBlur={e => {if(!e.currentTarget.parentElement?.contains(e.relatedTarget as Node)) setOpen(false);}}
    onKeyDown={e => {if(e.key==='ArrowDown'){e.preventDefault();setOpen(true);setActive(n=>Math.min(n+1,results.length-1));}else if(e.key==='ArrowUp'){e.preventDefault();setActive(n=>Math.max(0,n-1));}else if(e.key==='Enter'&&open&&results[active]){e.preventDefault();pick(results[active]);}else if(e.key==='Escape')setOpen(false);}} />
    {open && <ul id={`${id}-list`} role="listbox">{results.map((p,i)=><li key={p.id} role="option" id={`${id}-${i}`} aria-selected={i===active}><button type="button" onMouseDown={e=>e.preventDefault()} onClick={()=>pick(p)} onMouseEnter={()=>setActive(i)}><strong>{p.name}</strong><small>{p.aliases || (p.id.startsWith('osm')?'OpenStreetMap building':'Local landmark')}</small></button></li>)}{!results.length&&<li className="fine">No named match. Pick any supported point on the map.</li>}</ul>}</div></div>;
}
