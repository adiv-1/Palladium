const tabs = new Set(['live', 'response', 'ambassadors', 'routes', 'incidents', 'activity', 'learning', 'pipeline', 'controls']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Keep existing #incident=UUID, #activity and #learning links working after login.
export function readNavigation(hash = '') {
  const fragment = hash.replace(/^#/, '');
  const incident = new URLSearchParams(fragment).get('incident');
  if (incident && uuid.test(incident)) return {tab: 'incidents', incident};
  return {tab: tabs.has(fragment) ? fragment : 'live', incident: null};
}

export function navigationHash(tab, incident = null) {
  return incident && uuid.test(incident) ? `#incident=${incident}` : `#${tabs.has(tab) ? tab : 'live'}`;
}
