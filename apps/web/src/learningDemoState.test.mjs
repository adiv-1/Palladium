import test from 'node:test';
import assert from 'node:assert/strict';
import {LEARNING_ROLES,LEARNING_DEMO_DURATION,learningPreviewEligible,learningDemoStages,learningDemoOutput} from './learningDemoState.mjs';

test('the walkthrough requires an actual confirmed decision and excludes Trash',()=>{
  for(const review_status of ['pending','needs_review','dismissed',undefined])assert.equal(learningPreviewEligible({review_status}),false);
  assert.equal(learningPreviewEligible({review_status:'confirmed'}),true);
  assert.equal(learningPreviewEligible({review_status:'confirmed',record:{queue_deleted:{active:true}}}),false);
});

test('stage projection has one active role and labels every completion as a demonstration',()=>{
  assert.ok(learningDemoStages(0).every(s=>s.status==='demo_pending'));
  const stages=learningDemoStages(LEARNING_ROLES[0].duration+500);
  assert.equal(stages[0].status,'demo_complete');assert.equal(stages[1].status,'demo_running');assert.equal(stages.filter(s=>s.status==='demo_running').length,1);
  assert.ok(learningDemoStages(LEARNING_DEMO_DURATION+1000).every(s=>s.status==='demo_complete'));
  for(const elapsed of [-10,NaN,Infinity])assert.ok(learningDemoStages(elapsed).every(s=>s.status==='demo_pending'));
});

test('all illustrated outputs remain explicitly simulated and cannot claim model execution',()=>{
  const incident={id:'example-incident',review_status:'confirmed',record:{source_sha256:'original-hash',secret:'not-in-output',geolocation:{latitude:34,longitude:-118}}};
  const before=structuredClone(incident);
  for(const role of LEARNING_ROLES){
    const output=learningDemoOutput(incident,role.id);
    assert.equal(output.simulation,true);assert.equal(output.training_started,false);assert.equal(output.model_published,false);assert.equal(output.live_model_changed,false);
    assert.doesNotMatch(JSON.stringify(output),/not-in-output|latitude|longitude/);
  }
  assert.deepEqual(incident,before);
  const router=learningDemoOutput(incident,'router');assert.equal(router.planned_output.source_sha256,'original-hash');
  const trainer=learningDemoOutput(incident,'trainer');assert.equal(trainer.planned_output.base_model,'Qwen/Qwen3-VL-4B-Instruct');assert.equal(trainer.planned_output.moss_retrained,false);assert.equal(trainer.planned_output.optimizer_steps_performed,0);
});

test('the Hugging Face preview matches the real adapter-only publication allowlist',()=>{
  const output=learningDemoOutput({id:'example-id',record:{}},'registry').planned_output;
  assert.deepEqual(output.planned_files,['adapter_model.safetensors','adapter_config.json','evaluation.json','manifest.json','README.md']);
  assert.equal(output.upload_performed,false);assert.match(output.illustrative_subfolder,/^versions\/demo-/);
  assert.ok(output.excluded.includes('Original footage'));
  assert.equal(learningDemoOutput({record:{}},'release').planned_output.automatic_activation,false);
});
