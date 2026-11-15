import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import {demoResponders,requiresReview,responseRequested,monthBefore} from './responseState.mjs';
import {incidentLocation} from './campusModel.mjs';
import {deriveIncidentCard,updateIncidentQueue} from './incidentQueueState.mjs';
const bundle=JSON.parse(gunzipSync(readFileSync(new URL('../../../public-data/activity/2026-11-14/forecast-bundle.json.gz',import.meta.url))));

test('past month uses calendar dates and clamps month ends',()=>{
  assert.equal(monthBefore('2026-11-15'),'2026-10-15');
  assert.equal(monthBefore('2026-01-05'),'2025-12-05');
  assert.equal(monthBefore('2026-03-31'),'2026-02-28');
  assert.equal(monthBefore('2024-03-31'),'2024-02-29');
});

test('red review indicators distinguish model flags, manual bookmarks and human decisions',()=>{
  const incident={id:'video',review_status:'pending',severity:'REVIEW_REQUIRED',record:{trigger_origin:'model'}};
  assert.equal(requiresReview(incident),true);assert.equal(deriveIncidentCard(incident).requiresReview,true);
  assert.equal(responseRequested(incident),false);
  incident.record.trigger_origin='reviewer';assert.equal(requiresReview(incident),false);
  incident.review_status='needs_review';assert.equal(requiresReview(incident),true);assert.equal(responseRequested(incident),true);
  incident.review_status='dismissed';assert.equal(requiresReview(incident),false);assert.equal(responseRequested(incident),false);
  incident.review_status='confirmed';incident.record.queue_deleted={active:true};assert.equal(requiresReview(incident),false);assert.equal(responseRequested(incident),false);
});

test('a human classification updates the same record in Live and Incident response',()=>{
  const pending={id:'video',created_at:'2026-11-15',review_status:'pending',severity:'WATCH',record:{trigger_origin:'reviewer'}};
  const classified={...pending,review_status:'needs_review'};
  const queue=updateIncidentQueue([pending],classified);
  assert.equal(queue.length,1);assert.equal(queue[0].id,pending.id);assert.equal(queue.filter(responseRequested).length,1);assert.equal(deriveIncidentCard(queue[0]).requiresReview,true);
  const dismissed={...classified,review_status:'dismissed'};assert.equal(updateIncidentQueue(queue,dismissed).filter(responseRequested).length,0);
});

test('reviewer-supplied areas work for uploads without impersonating camera GPS',()=>{
  const gps={status:'active',provenance:'browser_permission',latitude:34.02,longitude:-118.28,accuracy_m:8,measured_at:1700000000000};
  const input={record:{geolocation:gps,reviewer_location:{active:true,provenance:'reviewer_supplied',precision:'approximate',latitude:34.02145,longitude:-118.2904,label:'Reviewer-selected area'}}};
  const before=structuredClone(input),location=incidentLocation(input,'upload');
  assert.deepEqual(location.point,[-118.2904,34.02145]);assert.equal(location.accuracy,null);assert.equal(location.measuredAt,null);assert.equal(location.provenance,'reviewer_supplied');assert.deepEqual(input,before);
  input.record.reviewer_location.active=false;assert.equal(incidentLocation(input,'upload'),null);assert.equal(incidentLocation(input,'phone').accuracy,8);
  input.record.reviewer_location.active=true;input.record.reviewer_location.latitude=NaN;assert.equal(incidentLocation(input,'upload'),null);
});

test('four demo responders use actual connected-network points, never actual officer locations',()=>{
  const point=[-118.2904,34.02145],units=demoResponders(bundle,point);
  assert.equal(units.length,4);assert.equal(new Set(units.map(u=>u.point.join(','))).size,4);
  const nodes=new Set(bundle.graph.largest_component.map(i=>bundle.graph.nodes[i].join(',')));
  for(const unit of units){assert.equal(nodes.has(unit.point.join(',')),true);assert.match(unit.name,/Demo/);assert.ok(unit.distance>=100&&unit.distance<=500)}
  for(const [i,unit] of units.entries())for(const other of units.slice(i+1))assert.ok(Math.hypot((unit.point[0]-other.point[0])*92300,(unit.point[1]-other.point[1])*111320)>=75);
  assert.deepEqual(demoResponders(bundle,point),units);
  assert.deepEqual(demoResponders(bundle,[-120,35]),[]);
});
