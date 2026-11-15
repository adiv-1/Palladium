import {useEffect, useId, useMemo, useState} from 'react';
import {searchPlaces} from './analysis';
import {CATEGORY_LABEL} from './buildings';
import type {Building} from './types';

// Text box with a live dropdown of matching campus places.
export function PlaceInput({label, buildings, value, onPick, placeholder}: {
  label: string; buildings: Building[]; value: Building | null; onPick: (b: Building) => void; placeholder: string;
}) {
  const [text, setText] = useState(value?.name ?? '');
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const id = useId();
  useEffect(() => setText(value?.name ?? ''), [value]);
  const results = useMemo(() => searchPlaces(buildings, text), [buildings, text]);
  const show = open && text.trim().length > 0 && text !== value?.name && results.length > 0;
  const pick = (b: Building) => {onPick(b); setOpen(false);};
  return (
    <div className="place-input">
      <label htmlFor={id}>{label}</label>
      <input
        id={id} value={text} placeholder={placeholder} autoComplete="off" role="combobox" aria-expanded={show} aria-controls={`${id}-list`}
        onChange={(e) => {setText(e.target.value); setOpen(true); setHi(0);}}
        onFocus={(e) => {e.target.select(); setOpen(true);}}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {e.preventDefault(); setHi((h) => Math.min(h + 1, results.length - 1));}
          else if (e.key === 'ArrowUp') {e.preventDefault(); setHi((h) => Math.max(h - 1, 0));}
          else if (e.key === 'Enter' && show) {e.preventDefault(); pick(results[hi]);}
          else if (e.key === 'Escape') setOpen(false);
        }}
      />
      {show && (
        <ul className="place-list" id={`${id}-list`} role="listbox">
          {results.map((b, i) => (
            <li key={b.id} role="option" aria-selected={i === hi} className={i === hi ? 'hi' : ''}
              onMouseDown={(e) => {e.preventDefault(); pick(b);}} onMouseEnter={() => setHi(i)}>
              <strong>{b.name}</strong>
              <small>{CATEGORY_LABEL[b.category]}{b.tags.short_name ? ` · ${b.tags.short_name}` : ''}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
