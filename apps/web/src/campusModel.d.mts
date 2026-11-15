type Data = Record<string, any>;
export function campusModel(bundle: Data, state: Data): {slot:number;values:number[][];cells:Data[];posts:Data[];covered:number;coverage:number;streets:Data};
export function incidentLocation(incident: Data, sourceKind?: string): {point:[number,number];accuracy:number|null;measuredAt:number|null;provenance:string;label:string} | null;
export function responseRoute(bundle: Data, staging: number[], target: number[]): Data;
export function demoTransition(state: any, action: any): any;
export function pointAlongPath(path: number[][], fraction: number): [number,number] | null;
