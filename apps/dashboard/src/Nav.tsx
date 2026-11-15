import {useEffect, useState} from 'react';
import {dashboardRoom} from './bus';
import {useTheme} from './theme';

export type Route = 'trends' | 'incidents' | 'live' | 'about';
const TABS: [Route, string][] = [['trends', 'Trends'], ['incidents', 'Incidents'], ['live', 'Live'], ['about', 'About']];

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

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button type="button" className="theme-toggle" onClick={toggle} aria-label={`Switch to ${next} mode`} title={`Switch to ${next} mode`}>
      {theme === 'dark' ? (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden>
          <circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z" />
        </svg>
      )}
    </button>
  );
}

export function Nav({active}: {active: Route}) {
  return (
    <div className="brand-nav">
      <a className="logo" href="#/trends"><i /><strong>SafeSC</strong></a>
      <nav className="tabs" aria-label="Pages">
        {TABS.map(([id, label]) => (
          <a key={id} href={`#/${id}`} className={id === active ? 'on' : ''} aria-current={id === active ? 'page' : undefined}>
            {id === 'live' && <span className="pulse-dot" />}{label}
          </a>
        ))}
        <a href={studentLink()} target="_blank" rel="noreferrer" className="ext">Student app ↗</a>
      </nav>
      <ThemeToggle />
    </div>
  );
}
