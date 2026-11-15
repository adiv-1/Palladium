import {createContext, useContext, useState, type Dispatch, type ReactNode, type SetStateAction} from 'react';

export type Point = [number, number];
export type Place = {id: string; name: string; point: Point; aliases?: string; footprint?: Point[]; tags?: Record<string, string>};
export type CampusState = {
  mode: 'explore' | 'routes' | 'posts' | 'history';
  date: string; hour: number; minute: number; family: string;
  threeD: boolean; basemap: 'streets' | 'imagery'; surveys: boolean;
  selected: number | null; staff: number; selectedPost: number | null;
  start: Place | null; end: Place | null; detour: number;
  historyStart: string; historyEnd: string; category: string; selectedReport: string | null;
};
export type ResponseDemo = {
  staging: Place | null; phase: 'idle' | 'en_route' | 'awaiting' | 'on_scene';
  startedAt?: number; path?: Point[];
};
export function losAngelesParts(date = new Date()) {
  return Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(date).map(p => [p.type, p.value]));
}
export function localDate(offset = 0) {
  const p = losAngelesParts();
  const d = new Date(`${p.year}-${p.month}-${p.day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}
function initialCampus(): CampusState {
  const p = losAngelesParts();
  return {mode: 'explore', date: localDate(), hour: +p.hour, minute: +p.minute, family: 'person',
    threeD: false, basemap: 'streets', surveys: false, selected: null, staff: 3, selectedPost: null,
    start: {id:'leavey',name:'Leavey Library',point:[-118.2829,34.0216]}, end: null, detour: .3, historyStart: '2016-01-01', historyEnd: localDate(), category: '', selectedReport: null};
}
type State = {
  campus: CampusState; setCampus: Dispatch<SetStateAction<CampusState>>;
  demos: Record<string, ResponseDemo>; setDemos: Dispatch<SetStateAction<Record<string, ResponseDemo>>>;
};
const Context = createContext<State | null>(null);
export function WorkspaceStateProvider({children}: {children: ReactNode}) {
  const [campus, setCampus] = useState(initialCampus);
  // Deliberately memory-only: no review mutations, storage, dispatch API, or phone bus.
  const [demos, setDemos] = useState<Record<string, ResponseDemo>>({});
  return <Context.Provider value={{campus, setCampus, demos, setDemos}}>{children}</Context.Provider>;
}
export function useWorkspaceState() {
  const value = useContext(Context);
  if (!value) throw Error('Workspace state is unavailable');
  return value;
}
