import {test} from 'node:test';
import assert from 'node:assert/strict';
import {makeNetwork,routes,slotFor} from './activityRouting.mjs';

function fixture(){
  const nodes=[[-118.285,34.022],[-118.284,34.022],[-118.283,34.022],[-118.284,34.0225]];
  const graph={nodes,edges:[[0,1,100,0,0],[1,2,100,0,0],[0,3,110,1,1],[3,2,110,1,1]],names:['High activity','Lower activity'],largest_component:[0,1,2,3]};
  return {graph,boundary:{geometry:{coordinates:[[[-118.29,34.02],[-118.28,34.02],[-118.28,34.03],[-118.29,34.03],[-118.29,34.02]]]}},weekly:{person:Array.from({length:56},()=>[[.1,.1,0,1,10],[.01,.01,0,0,.1]])},model:{recent_support:[[30,30],[30,30]]},density_thresholds:{person:{high:5}}};
}
test('finds the shortest route and lower-activity candidate within detour budget',()=>{
  const b=fixture(),n=makeNetwork(b.graph);
  const r=routes(n,b,b.graph.nodes[0],b.graph.nodes[2],'2026-11-14',18,'person',.3);
  assert.equal(r.direct.meters,200);assert.equal(r.alternative.meters,220);assert.ok(r.changed);
  assert.ok(r.alternative.activity<r.direct.activity);
  const limited=routes(n,b,b.graph.nodes[0],b.graph.nodes[2],'2026-11-14',18,'person',.05);
  assert.equal(limited.changed,false);
});
test('midnight traversal changes weekday/time slot in Los Angeles local calendar',()=>{
  assert.equal(slotFor('2026-11-15',21),55);
  assert.equal(slotFor('2026-11-15',21,181),0);
  assert.equal(slotFor('2026-11-14',0),40);
});
test('rejects out-of-zone and disconnected/unmapped endpoints',()=>{
  const b=fixture(),n=makeNetwork(b.graph);
  assert.throws(()=>routes(n,b,[-118.4,34.02],b.graph.nodes[2],'2026-11-14',18,'person'),/inside/);
  assert.throws(()=>routes(n,b,[-118.289,34.029],b.graph.nodes[2],'2026-11-14',18,'person'),/100 m/);
  const disconnected={...b.graph,edges:[[0,1,100,0,0],[2,3,110,1,1]],largest_component:[0,1,2,3]};
  assert.throws(()=>routes(makeNetwork(disconnected),b,b.graph.nodes[0],b.graph.nodes[2],'2026-11-14',18,'person'),/No connected/);
});
