import {useEffect, useState} from 'react';
import buildingsUrl from './campusPlaces.json?url';
import type {Place} from './WorkspaceState';
type Data = Record<string, any>;
let bundleRequest: Promise<Data> | undefined;
let placesRequest: Promise<Place[]> | undefined;
export const landmarks: Place[] = [
  {id:'village',name:'USC Village · Jefferson / Hoover',point:[-118.2847,34.0258]},
  {id:'leavey',name:'Leavey Library',point:[-118.2829,34.0216]},
  {id:'tommy',name:'Tommy Trojan',point:[-118.2854,34.0206]},
  {id:'expo',name:'Expo Park / USC station',point:[-118.285,34.0184]},
  {id:'jefferson',name:'Jefferson / USC station',point:[-118.2781,34.022]},
  {id:'washington',name:'Washington / Vermont',point:[-118.2915,34.0399]},
  {id:'adams',name:'Adams / Normandie',point:[-118.3004,34.0327]},
  {id:'mlk',name:'MLK / Vermont · north side',point:[-118.2915,34.0112]},
];
export function loadActivityBundle() {
  if (!bundleRequest) bundleRequest = fetch('/api/activity/exploration-bundle').then(async r => {
    if (!r.ok) {let detail = 'Activity analysis is unavailable.'; try {detail = (await r.json()).detail || detail;} catch {} throw Error(detail);}
    return r.json();
  }).catch(error => {bundleRequest = undefined; throw error;});
  return bundleRequest;
}
export function useActivityBundle() {
  const [bundle, setBundle] = useState<Data | null>(null), [error, setError] = useState(''), [attempt, retry] = useState(0);
  useEffect(() => {let active = true; setError(''); loadActivityBundle().then(d => active && setBundle(d)).catch(e => active && setError(e.message)); return () => {active = false;};}, [attempt]);
  return {bundle, error, retry: () => retry(n => n + 1)};
}
export function usePlaces() {
  const [places, setPlaces] = useState<Place[]>(landmarks), [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    if (!placesRequest) placesRequest = fetch(buildingsUrl).then(r => {if (!r.ok || !r.headers.get('content-type')?.includes('json')) throw Error('Named buildings unavailable; landmarks and map picking remain available.'); return r.json();}).then(data => [
      ...landmarks, ...data.buildings.map((b: Data) => ({id:b.id, name:b.name, point:[b.lng,b.lat], footprint:b.footprint, tags:b.tags, aliases:[b.tags?.short_name,b.tags?.alt_name].filter(Boolean).join(' ')})),
    ]).catch(e => {placesRequest = undefined; throw e;});
    placesRequest.then(d => active && setPlaces(d)).catch(e => active && setError(e.message));
    return () => {active = false;};
  }, []);
  return {places, error};
}
