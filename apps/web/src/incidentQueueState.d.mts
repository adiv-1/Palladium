export interface IncidentCardDisplay {
  title:string;actionTitle:string;manual:boolean;reviewLabel:string;reviewTone:string;
  reviewTakeaway:string|null;reviewTakeawayLabel:string;initialObservation:string|null;
  contextText:string|null;contextLabel:string;held:boolean;locationLabel:string|null;
  reviewStatus:string;duration:number|null;lighting:string|null;mediaUnavailable:boolean;
}
export function deriveIncidentCard(incident:Record<string,any>):IncidentCardDisplay;
