import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {readNavigation,navigationHash} from './workspaceNavigation.mjs';
import {campusModel,demoTransition,incidentLocation,pointAlongPath,responseRoute} from './campusModel.mjs';
import {makeNetwork,routes} from './activityRouting.mjs';

test('the three solution tabs and legacy deep links resolve without losing the selected incident',()=>{
  for(const tab of ['live','response','ambassadors','routes','activity','learning','incidents','controls']) assert.deepEqual(readNavigation(navigationHash(tab)),{tab,incident:null});
  const id='12a85670-1742-4f12-8473-052970225611';
  assert.deepEqual(readNavigation('#incident='+id),{tab:'incidents',incident:id});
  assert.equal(readNavigation('#incident=../../elsewhere').incident,null);
  assert.equal(readNavigation('#missing').tab,'live');
});

const bundle=JSON.parse(gunzipSync(readFileSync(new URL('../../../public-data/activity/2026-11-14/forecast-bundle.json.gz',import.meta.url))));
test('the real snapshot yields identical planning and routing when only map presentation changes',()=>{
  const state={date:'2026-11-15',hour:18,minute:30,family:'person',staff:3,threeD:false,basemap:'streets'};
  const flat=campusModel(bundle,state),threeD=campusModel(bundle,{...state,threeD:true,basemap:'imagery'});
  assert.deepEqual(threeD,flat);
  assert.equal(flat.cells.length,172);
  assert.equal(flat.posts.length,3);
  assert.equal(new Set(flat.posts.at(-1).covered_cells).size,flat.posts.at(-1).covered_cells.length);
  assert.ok(flat.coverage>=0&&flat.coverage<=1);
  const start=[-118.2829,34.0216],end=[-118.2915,34.0399];
  const route=routes(makeNetwork(bundle.graph),bundle,start,end,state.date,18.5,'person',.3);
  assert.ok(route.alternative.minutes<=route.direct.minutes*1.3);
  assert.deepEqual(routes(makeNetwork(bundle.graph),bundle,start,end,state.date,18.5,'person',.3),route);
  const more=campusModel(bundle,{...state,staff:4});
  assert.deepEqual(more.posts.slice(0,3),flat.posts);
  assert.ok(more.covered>=flat.covered);
});

test('saved camera fixes are optional and never inferred for uploads or remote video',()=>{
  const fix={status:'active',provenance:'browser_permission',latitude:34.0216,longitude:-118.2829,accuracy_m:8,measured_at:1700000000000};
  const incident={review_status:'pending',record:{geolocation:fix}};
  assert.deepEqual(incidentLocation(incident,'phone').point,[-118.2829,34.0216]);
  for(const kind of ['upload','rtsp'])assert.equal(incidentLocation(incident,kind),null);
  for(const change of [{status:'stale'},{status:'denied'},{status:'disabled'},{latitude:NaN},{longitude:181},{accuracy_m:-1},{provenance:'inferred'},{measured_at:null}]) assert.equal(incidentLocation({record:{geolocation:{...fix,...change}}},'phone'),null);
  assert.equal(incidentLocation({record:{}},'phone'),null);
});

test('response animation uses the real connected graph and requires a separate reviewer arrival action',()=>{
  const incident={review_status:'pending',record:{reviewed_label:{title:'Preserved label'}}};
  const before=structuredClone(incident);
  const route=responseRoute(bundle,[-118.2829,34.0216],[-118.2854,34.0206]);
  assert.ok(route.coordinates.length>1);
  assert.throws(()=>responseRoute(bundle,[-118.2829,34.0216],[-120,35]),/inside/);
  let state={staging:null,phase:'idle'};
  assert.deepEqual(demoTransition(state,{type:'arrived'}),state);
  state=demoTransition(state,{type:'staging',place:{id:'test',point:route.coordinates[0]}});
  state=demoTransition(state,{type:'start',path:route.coordinates,now:100});
  assert.equal(state.phase,'en_route');
  state=demoTransition(state,{type:'await'});
  assert.equal(state.phase,'awaiting');
  assert.equal(demoTransition(state,{type:'arrived'}).phase,'on_scene');
  assert.deepEqual(demoTransition(state,{type:'reset'}),{staging:null,phase:'idle'});
  assert.deepEqual(incident,before);
});

test('illustrative motion retains route endpoints and does not fabricate out-of-range positions',()=>{
  const path=[[0,0],[.001,0],[.001,.001]];
  assert.deepEqual(pointAlongPath(path,-1),path[0]);
  assert.deepEqual(pointAlongPath(path,2),path.at(-1));
  assert.equal(pointAlongPath([],1),null);
});

test('all three bundled demo recordings are the exact uploader-reviewed GitHub originals',()=>{
  const root=new URL('../../../demo/videos/',import.meta.url);
  const manifest=JSON.parse(readFileSync(new URL('manifest.json',root)));
  assert.equal(manifest.files.length,3);
  for(const item of manifest.files){const bytes=readFileSync(new URL(item.file,root));assert.equal(bytes.length,item.bytes);assert.equal(createHash('sha256').update(bytes).digest('hex'),item.sha256);}
});
