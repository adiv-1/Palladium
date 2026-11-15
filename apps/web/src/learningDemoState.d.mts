type Data=Record<string,any>;
export const LEARNING_ROLES:{id:string;name:string;worker:string;description:string;duration:number}[];
export const LEARNING_DEMO_DURATION:number;
export function learningPreviewEligible(incident:Data):boolean;
export function learningDemoStages(elapsed?:number):(typeof LEARNING_ROLES[number]&{start:number;end:number;progress:number;status:string})[];
export function learningDemoOutput(incident:Data,roleId:string):Data;
