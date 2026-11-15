import test from 'node:test';
import assert from 'node:assert/strict';
import {deriveIncidentCard} from './incidentQueueState.mjs';
const incident=record=>({id:'sample',review_status:'pending',start_time:1,end_time:5,record});
test('uploader context stays separate from initial and secondary AI findings',()=>{
 const input=incident({action:'object_taking',observable_events:[{observation:'A person carries an object.'}],reviewer_context:{text:'Staged jacket reenactment.',provenance:'uploader statement'},retention_hold:{active:true},clip_review:{report:{takeaway:'A person retrieves a garment.',alert_support:'uncertain'}}});
 const before=JSON.stringify(input),card=deriveIncidentCard(input);
 assert.equal(card.title,'Object-taking flagged');assert.equal(card.contextLabel,'Uploader context');assert.equal(card.contextText,'Staged jacket reenactment.');assert.equal(card.initialObservation,'A person carries an object.');assert.equal(card.reviewLabel,'Secondary review uncertain');assert.equal(card.held,true);assert.equal(JSON.stringify(input),before);
});
test('secondary disagreement is visible and a manual review can use the latest takeaway',()=>{
 const auto=deriveIncidentCard(incident({action:'physical_contact',clip_review:{report:{takeaway:'No clear contact is visible.',alert_support:'not_supported'}}}));
 assert.equal(auto.title,'Physical contact flagged');assert.equal(auto.reviewLabel,'Secondary review disagrees');
 const manual=deriveIncidentCard(incident({trigger_origin:'reviewer',clip_review:{report:{takeaway:'No physical harm is visible.',alert_support:'not_applicable'}}}));
 assert.equal(manual.title,'No physical harm is visible.');assert.equal(manual.reviewLabel,'Secondary review complete');
});
test('pending jobs override prior review status without claiming a new success',()=>{
 const input=incident({clip_review:{report:{takeaway:'An earlier finding.',alert_support:'supported'}}});input.processing_summary={review_brief:{status:'running'}};
 const card=deriveIncidentCard(input);assert.equal(card.reviewLabel,'Secondary review running');assert.equal(card.reviewTakeawayLabel,'Previous secondary review');
 const missing=incident({});assert.equal(deriveIncidentCard(missing).reviewLabel,'Secondary review not available');
 missing.processing_summary={review_brief:{status:'failed'}};assert.equal(deriveIncidentCard(missing).reviewLabel,'Secondary review failed');
});
test('GPS labels require real coordinates before claiming a saved phone location',()=>{
 assert.equal(deriveIncidentCard(incident({geolocation:{status:'active'}})).locationLabel,'Phone location pending');
 assert.equal(deriveIncidentCard(incident({geolocation:{status:'active',latitude:34,longitude:-118,accuracy_m:12.3}})).locationLabel,'Phone location · ±12 m');
 assert.equal(deriveIncidentCard(incident({geolocation:{status:'disabled',latitude:34,longitude:-118,accuracy_m:12.3}})).locationLabel,'Phone location unavailable');
 assert.equal(deriveIncidentCard(incident({})).locationLabel,null);
});
test('historical failures do not hide an available completed secondary report',()=>{
 const input=incident({clip_review:{report:{takeaway:'A visible object retrieval.',alert_support:'supported'}}});
 input.processing_summary={review_brief:{status:'completed'},failed:2};
 assert.equal(deriveIncidentCard(input).reviewLabel,'Secondary review complete');
});
