type Data=Record<string,any>;
export const ACTIVE_RUN_STATES=new Set(['queued','running','cancelling']);
export const WORKFLOW_ROLES=[
  {id:'review',title:'Review & permission',role:'Reviewer',description:'Approved visible labels and explicit reuse permission.'},
  {id:'dataset',title:'Dataset preparation',role:'Dataset worker',description:'Validate the snapshot, recording splits and source hashes.'},
  {id:'training',title:'Candidate training',role:'Training worker',description:'Load the base model and optimize a separate candidate.'},
  {id:'evaluation',title:'Evaluation',role:'Evaluation worker',description:'Compare recorded baseline and candidate results.'},
  {id:'publishing',title:'Publish version',role:'Registry worker',description:'Publish versioned adapter weights and sanitized evaluation to the private registry.'},
];
export function workflowRoleStates(run?:Data){
  const workflow=run?.record?.workflow||{},events:Data[]=Array.isArray(workflow.events)?workflow.events:[];
  const active=ACTIVE_RUN_STATES.has(workflow.status||run?.status);
  const terminal=['failed','cancelled'].includes(workflow.status||run?.status)?workflow.status||run?.status:null;
  const lastWork=[...events].reverse().find(e=>['preparing','loading','training','evaluating','publishing'].includes(e.stage)&&e.status!=='completed');
  const preparationDone=events.some(e=>e.stage==='preparing'&&e.status==='completed');
  const progress=workflow.progress||{},optimizerStage=['training','evaluating','publishing','completed'].includes(workflow.stage),stepsDone=optimizerStage&&Number.isFinite(progress.step)&&Number.isFinite(progress.total_steps)&&progress.total_steps>0&&progress.step>=progress.total_steps;
  return WORKFLOW_ROLES.map(role=>{
    if(role.id==='publishing'){const registry=run?.record?.registry;const event=events.filter(e=>e.stage==='publishing').at(-1);return {...role,status:registry?.status==='published'?'completed':registry?.status==='failed'?'failed':registry?.status==='not_configured'?'not_configured':active&&workflow.stage==='publishing'?'running':terminal&&lastWork?.stage==='publishing'?terminal:'pending',timestamp:event?.timestamp||registry?.published_at||null,message:event?.message||null}}
    const stages=role.id==='review'||role.id==='dataset'?['preparing']:role.id==='training'?['loading','training']:['evaluating'];
    const relevant=events.filter(e=>stages.includes(e.stage)),last=relevant.at(-1);
    const completed=role.id==='review'||role.id==='dataset'?preparationDone:role.id==='training'?stepsDone||events.some(e=>e.stage==='training'&&e.status==='completed'||e.stage==='evaluating'):events.some(e=>e.stage==='evaluating'&&e.status==='completed')||workflow.status==='completed'&&!!run?.record?.evaluation;
    const current=stages.includes(workflow.stage);
    const status=completed?'completed':terminal&&stages.includes(lastWork?.stage)?terminal:active&&current?'running':'pending';
    return {...role,status,timestamp:last?.timestamp||null,message:last?.message||null};
  });
}
