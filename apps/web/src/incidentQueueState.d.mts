export interface IncidentCardDisplay {
  title:string;manual:boolean;initialObservation:string|null;
  held:boolean;locationLabel:string|null;
  reviewStatus:string;duration:number|null;lighting:string|null;mediaUnavailable:boolean;
}
export function deriveIncidentCard(incident:Record<string,any>):IncidentCardDisplay;
