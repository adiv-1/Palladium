export type IncidentType = 'person_down' | 'altercation' | 'rack_activity' | 'other';
export type Severity = 'HIGH_PRIORITY_REVIEW' | 'REVIEW_REQUIRED' | 'WATCH';
export type Status = 'unreviewed' | 'approved' | 'dismissed';

// Where a number on screen comes from. Every layer and counter carries one of these.
export type Provenance = 'real' | 'demo' | 'simulated' | 'estimate' | 'live';

export type LngLat = [number, number];

export type Incident = {
  id: string;
  type: IncidentType;
  severity: Severity;
  position: LngLat;
  place: string;
  cameraId: string;
  createdAt: number;
  status: Status;
  observation: string;
  evidence: string[];
  uncertainties: string[];
  confidence: number;
  origin: 'demo' | 'live';
  thumbnail?: string;
  officerId?: string;
  decidedAt?: number;
};

export type Camera = {
  id: string;
  position: LngLat;
  heading: number; // degrees clockwise from north
  fov: number;
  range: number; // metres
  watchedLive: boolean;
  near: string;
};

export type Officer = {
  id: string;
  callsign: string;
  mode: 'foot' | 'bike' | 'cart';
  position: LngLat;
  busyWith?: string;
};

export type Rack = {id: string; position: LngLat; capacity: number | null};
export type Building = {id: string; name: string; position: LngLat};

export type HistoryPoint = {position: LngLat; hour: number; type: IncidentType};

export type Dispatch = {incidentId: string; officerId: string; path: [number, number, number][]; startedAt: number; seconds: number};

export type Funnel = {frames: number; candidates: number; vlmChecks: number; alerts: number; approved: number};

export const INCIDENT_LABEL: Record<IncidentType, string> = {
  person_down: 'Person down',
  altercation: 'Physical altercation',
  rack_activity: 'Activity at bike rack',
  other: 'Possible incident',
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  HIGH_PRIORITY_REVIEW: 'High priority',
  REVIEW_REQUIRED: 'Review',
  WATCH: 'Watch',
};
