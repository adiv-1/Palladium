export type IncidentType = 'person_down' | 'altercation' | 'rack_activity' | 'other';
export type Severity = 'HIGH_PRIORITY_REVIEW' | 'REVIEW_REQUIRED' | 'WATCH';
// unreviewed -> approved (units dispatched) -> resolved, or dismissed
export type Status = 'unreviewed' | 'approved' | 'resolved' | 'dismissed';

// Where a number on screen comes from. Every layer and counter carries one of these.
export type Provenance = 'real' | 'demo' | 'simulated' | 'estimate' | 'live';

export type LngLat = [number, number];

export type Incident = {
  id: string;
  type: IncidentType;
  severity: Severity;
  position: LngLat;
  place: string;
  buildingId?: string;
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
  dispatchId?: string;
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

export type UnitStatus = 'available' | 'assigned' | 'en_route' | 'on_scene';

export type Route = {from: LngLat; to: LngLat; path: LngLat[]; startedAt: number; seconds: number};

export type Officer = {
  id: string;
  callsign: string;
  mode: 'foot' | 'bike' | 'cart';
  position: LngLat;
  status: UnitStatus;
  dispatchId?: string;
  route?: Route;
  assignedAt?: number;
  /** @deprecated kept for older callers; true while status is not available */
  busyWith?: string;
};

export type Rack = {id: string; position: LngLat; capacity: number | null};

export type BuildingCategory = 'library' | 'residence' | 'recreation' | 'parking' | 'health' | 'dining' | 'academic' | 'other';

export type Building = {
  id: string;
  name: string;
  position: LngLat;
  footprint: LngLat[];
  tags: Record<string, string>;
  category: BuildingCategory;
};

export type HistoryPoint = {position: LngLat; hour: number; type: IncidentType};

export type Priority = 'P1' | 'P2' | 'P3';

export type DispatchEventKind = 'created' | 'notified' | 'acknowledged' | 'en_route' | 'on_scene' | 'cleared' | 'cancelled' | 'support';

export type DispatchEvent = {at: number; kind: DispatchEventKind; unitId?: string; note?: string};

export type DispatchRecord = {
  id: string;
  incidentId: string;
  unitIds: string[];
  priority: Priority;
  reason: string;
  support: string[];
  createdAt: number;
  status: 'active' | 'resolved' | 'cancelled';
  events: DispatchEvent[];
};

// Visual arc from a unit to its incident when the dispatch is sent.
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

export const UNIT_STATUS_LABEL: Record<UnitStatus, string> = {
  available: 'Available',
  assigned: 'Notified',
  en_route: 'En route',
  on_scene: 'On scene',
};
