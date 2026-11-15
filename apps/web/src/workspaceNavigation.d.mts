export type WorkspaceTab = 'live' | 'response' | 'ambassadors' | 'routes' | 'incidents' | 'activity' | 'learning' | 'controls';
export function readNavigation(hash?: string): {tab: WorkspaceTab; incident: string | null};
export function navigationHash(tab: WorkspaceTab, incident?: string | null): string;
