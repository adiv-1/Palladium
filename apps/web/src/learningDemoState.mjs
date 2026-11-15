// Presentation-only roles mapped to the existing reviewed-learning workflow.
// This module has no network, storage, training, or model-publication effects.
export const LEARNING_ROLES = [
  {id:'router',name:'Review router',worker:'Review policy',description:'Accept human-confirmed events and identify the original footage.',duration:1800},
  {id:'annotation',name:'Annotation reviewer',worker:'Human approval',description:'Require training reuse permission and a reviewed visible-action interval.',duration:2400},
  {id:'curator',name:'Dataset curator',worker:'Python worker',description:'Prepare four original frames per interval and freeze a versioned dataset.',duration:2200},
  {id:'validator',name:'Dataset validator',worker:'Provenance checks',description:'Check hashes, duplicate recordings and separate train / validation / test groups.',duration:2200},
  {id:'compute',name:'Compute coordinator',worker:'GPU queue',description:'Wait for live analysis to finish before allocating candidate-training compute.',duration:1800},
  {id:'trainer',name:'LoRA trainer',worker:'Qwen3-VL-4B',description:'Train a small attention adapter while base-model weights remain frozen.',duration:4000},
  {id:'evaluator',name:'Held-out evaluator',worker:'Parent vs candidate',description:'Compare missed actions, false alerts and valid outputs on excluded evaluation splits.',duration:2800},
  {id:'registry',name:'Registry publisher',worker:'Hugging Face · private',description:'Version adapter weights, configuration and sanitized evaluation metadata.',duration:3000},
  {id:'release',name:'Release reviewer',worker:'Manual activation',description:'Keep the live model unchanged until an evaluated candidate is explicitly approved.',duration:1800},
];
export const LEARNING_DEMO_DURATION=LEARNING_ROLES.reduce((total,role)=>total+role.duration,0);
export function learningPreviewEligible(incident){
  return incident?.review_status==='confirmed'&&incident.record?.queue_deleted?.active!==true;
}
export function learningDemoStages(elapsed=0){
  const safe=typeof elapsed==='number'&&Number.isFinite(elapsed)?Math.max(0,elapsed):0;
  let start=0;
  return LEARNING_ROLES.map(role=>{
    const end=start+role.duration;
    const stage={...role,start,end,progress:Math.max(0,Math.min(1,(safe-start)/role.duration)),status:safe>=end?'demo_complete':safe>start?'demo_running':'demo_pending'};
    start=end;return stage;
  });
}
export function learningDemoOutput(incident,roleId){
  const record=incident.record||{};
  const context={incident_id:incident.id,review_status:incident.review_status,source_sha256:record.source_sha256||null,original_clip_sha256:record.original_sha256||null};
  const outputs={
    router:{...context,next:'Check separate training permission and annotation approval'},
    annotation:{reuse_permission:'Must be checked in the real learning repository',annotation:'Human-reviewed action and exact visible interval required',confirmation_grants_training_permission:false},
    curator:{snapshot:'Illustrative immutable dataset version',frames_per_interval:4,splits:['train','validation','test'],grouping:'All clips from an original recording stay in one split'},
    validator:{checks:['Original hashes','Current permissions','Reviewed labels','Recording-group split isolation'],result:'Illustrated checks only; no dataset validation was executed'},
    compute:{worker:'Existing Runpod GPU',policy:'Live capture has priority',allocation:'No GPU allocated by this preview'},
    trainer:{base_model:'Qwen/Qwen3-VL-4B-Instruct',method:'LoRA',rank:8,target_modules:['q_proj','v_proj'],base_weights:'Frozen',moss_retrained:false,optimizer_steps_performed:0},
    evaluator:{comparison:'Parent adapter vs candidate',splits:['validation','test'],metrics:['Missed positive events','False alerts','Action accuracy','Invalid outputs'],result:'No new evaluation performed; no improvement is asserted'},
    registry:{destination:'Private Hugging Face model repository',illustrative_subfolder:`versions/demo-${String(incident.id).slice(0,8)}`,planned_files:['adapter_model.safetensors','adapter_config.json','evaluation.json','manifest.json','README.md'],excluded:['Original footage','Training frames','Private annotations','Per-example predictions'],versioning:['Immutable version name','Commit revision','Tag','SHA-256 checks'],upload_performed:false},
    release:{decision:'Human approval required after real evaluation',automatic_activation:false,live_model:'Current selected adapter remains active'},
  };
  return {simulation:true,role:roleId,training_started:false,model_published:false,live_model_changed:false,planned_output:outputs[roleId]||{message:'Select an agent role'}};
}
