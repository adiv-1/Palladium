import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('./trainingWorkflowState.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}});
const {workflowRoleStates}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
const event=(stage,status)=>({stage,status,timestamp:'2026-11-15T01:00:00Z',message:'Recorded worker event'});
const run=(stage,status,events=[],progress={})=>({status,record:{workflow:{stage,status,events,progress}}});
test('legacy experiments and queued runs do not invent completed workflow stages',()=>{
  assert.deepEqual(workflowRoleStates({status:'completed',record:{evaluation:{}}}).map(s=>s.status),['pending','pending','pending','pending','pending']);
  assert.deepEqual(workflowRoleStates(run('queued','queued')).map(s=>s.status),['pending','pending','pending','pending','pending']);
});
test('review and dataset readiness require recorded preparation validation',()=>{
  const loading=run('loading','running',[event('loading','running')]);
  assert.deepEqual(workflowRoleStates(loading).map(s=>s.status),['pending','pending','running','pending','pending']);
  loading.record.workflow.events.unshift(event('preparing','completed'));
  assert.deepEqual(workflowRoleStates(loading).map(s=>s.status),['completed','completed','running','pending','pending']);
});
test('optimizer progress shows completed training while actual candidate evaluation runs',()=>{
  const r=run('evaluating','running',[event('preparing','completed'),event('training','running'),event('evaluating','running')],{step:20,total_steps:20,loss:.15});
  assert.deepEqual(workflowRoleStates(r).map(s=>s.status),['completed','completed','completed','running','pending']);
  r.record.workflow.status='completed';r.record.evaluation={nonregression:false};
  assert.deepEqual(workflowRoleStates(r).map(s=>s.status),['completed','completed','completed','completed','pending']);
});
test('cancellation and failure stop the actual active worker without fake downstream completion',()=>{
  const r=run('cancelled','cancelled',[event('preparing','completed'),event('training','running'),event('cancelled','cancelled')],{step:3,total_steps:20});
  assert.deepEqual(workflowRoleStates(r).map(s=>s.status),['completed','completed','cancelled','pending','pending']);
  const failed=run('failed','failed',[event('preparing','running'),event('failed','failed')]);
  assert.deepEqual(workflowRoleStates(failed).map(s=>s.status),['failed','failed','pending','pending','pending']);
});

test('registry publication status never implies live deployment or hides publication failure',()=>{
 const r=run('publishing','running',[event('preparing','completed'),event('training','completed'),event('evaluating','completed'),event('publishing','running')],{step:20,total_steps:20});
 assert.deepEqual(workflowRoleStates(r).map(s=>s.status),['completed','completed','completed','completed','running']);
 r.record.registry={status:'failed',error:'Registry unavailable'};assert.equal(workflowRoleStates(r).at(-1).status,'failed');
 r.record.registry={status:'published',revision:'example'};assert.equal(workflowRoleStates(r).at(-1).status,'completed');
 r.record.registry={status:'not_configured'};assert.equal(workflowRoleStates(r).at(-1).status,'not_configured');
});

test('dataset preparation counters are never presented as completed optimizer updates',()=>{
 const r=run('queued','queued',[event('preparing','running'),event('queued','queued')],{step:3,total_steps:3});
 assert.equal(workflowRoleStates(r).find(s=>s.id==='training').status,'pending');
});
