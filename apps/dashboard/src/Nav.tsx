import {useEffect, useState} from 'react';
import {dashboardRoom} from './bus';
import {Logo} from './Logo';

export type Route = 'trends' | 'incidents' | 'live' | 'about';
const TABS: [Route, string][] = [['trends', 'Campus'], ['live', 'Live detection'], ['about', 'How it works'], ['incidents', 'Incidents']];

export function currentRoute(): Route {
  const r = location.hash.replace(/^#\/?/, '').split(/[?/]/)[0];
  return (['trends', 'incidents', 'live', 'about'] as string[]).includes(r) ? (r as Route) : 'trends';
}

export function useRoute() {
  const [route, setRoute] = useState<Route>(currentRoute());
  useEffect(() => {
    const on = () => setRoute(currentRoute());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}

export const studentLink = () => `${location.origin}${location.pathname}?app=1&room=${encodeURIComponent(dashboardRoom())}`;

export function Nav({active}: {active: Route}) {
  return (
    <div className="brand-nav">
      <a className="logo" href="#/trends" aria-label="Palladium"><Logo variant="mark" size={38} /><strong>PALLADIUM</strong></a>
      <nav className="tabs" aria-label="Pages">
        {TABS.map(([id, label]) => (
          <a key={id} href={`#/${id}`} className={id === active ? 'on' : ''} aria-current={id === active ? 'page' : undefined}>
            {id === 'live' && <span className="pulse-dot" />}{label}
          </a>
        ))}
        <a href={studentLink()} target="_blank" rel="noreferrer" className="ext">Student app ↗</a>
      </nav>
    </div>
  );
}
