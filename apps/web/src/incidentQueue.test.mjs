import test from 'node:test';
import assert from 'node:assert/strict';
import {deriveIncidentCard,updateIncidentQueue} from './incidentQueueState.mjs';
const incident=record=>({id:'sample',review_status:'pending',start_time:1,end_time:5,record});
test('unreviewed cards use neutral copy while preserving model output for audit',()=>{
 const input=incident({action:'object_taking',observable_events:[{observation:'A person carries an object.'}],retention_hold:{active:true}});
 const before=JSON.stringify(input),first=deriveIncidentCard(input);
 assert.equal(first.title,'Saved video for review');assert.equal(first.initialObservation,'A person carries an object.');assert.equal(first.held,true);assert.equal(JSON.stringify(input),before);
 input.record.action='physical_contact';input.record.observable_events[0].observation='Two people make physical contact.';
 const second=deriveIncidentCard(input);assert.equal(second.title,'Saved video for review');assert.equal(second.initialObservation,'Two people make physical contact.');
});
test('manual cards stay neutral until explicitly labeled',()=>{
 const input=incident({trigger_origin:'reviewer',observable_events:[{observation:'A reviewer saved this moment.'}],clip_review:{report:{takeaway:'Secondary suggestion.'}}});
 assert.equal(deriveIncidentCard(input).title,'Saved video for review');
 input.record.observable_events[0].observation='A different requested moment.';
 assert.equal(deriveIncidentCard(input).title,'Saved video for review');
 assert.equal(deriveIncidentCard(incident({trigger_origin:'reviewer'})).title,'Saved video for review');
});
test('uploader context and secondary review do not affect any queue display fields',()=>{
 for(const trigger_origin of ['model','reviewer']){
  const input=incident({trigger_origin,action:'object_taking',observable_events:[{observation:'A person retrieves an object.'}]});
  const expected=deriveIncidentCard(input);
  input.record.reviewer_context={text:'Uploader provided a staged description.',provenance:'uploader statement'};
  input.record.uploader_context='Another description.';
  input.record.clip_review={report:{takeaway:'Different secondary conclusion.',alert_support:'not_supported'}};
  input.processing_summary={review_brief:{status:'running'},failed:2};
  assert.deepEqual(deriveIncidentCard(input),expected);
  assert.equal('contextText' in expected,false);assert.equal('reviewTakeaway' in expected,false);assert.equal('reviewLabel' in expected,false);
 }
});
test('record IDs and filenames never choose card wording',()=>{
 const input=incident({action:'physical_contact',observable_events:[{observation:'A person makes contact.'}]});
 const expected=deriveIncidentCard(input);input.id='another-upload';input.record.source_file='quiet-video.mp4';
 assert.deepEqual(deriveIncidentCard(input),expected);
});
test('GPS labels require an active valid phone fix',()=>{
 assert.equal(deriveIncidentCard(incident({geolocation:{status:'active'}})).locationLabel,'Phone location pending');
 assert.equal(deriveIncidentCard(incident({geolocation:{status:'active',latitude:34,longitude:-118,accuracy_m:12.3}})).locationLabel,'Phone location · ±12 m');
 assert.equal(deriveIncidentCard(incident({geolocation:{status:'disabled',latitude:34,longitude:-118,accuracy_m:12.3}})).locationLabel,'Phone location unavailable');
 assert.equal(deriveIncidentCard(incident({})).locationLabel,null);
});

test('reviewed titles, descriptions and tags replace conflicting AI copy without mutating observations',()=>{
 const input=incident({action:'object_taking',observable_events:[{observation:'Incorrect bag description.'}],reviewed_label:{title:'A jacket is taken',description:'A person picks up the jacket and moves away.',action:'object_taking',tags:['jacket','staged'],provenance:'workspace_reviewer'}});
 const before=JSON.stringify(input),card=deriveIncidentCard(input);assert.equal(card.title,'A jacket is taken');assert.equal(card.description,'A person picks up the jacket and moves away.');assert.deepEqual(card.tags,['jacket','staged']);assert.equal(card.reviewed,true);assert.equal(card.initialObservation,'Incorrect bag description.');assert.equal(JSON.stringify(input),before);
});
test('soft-deletion events remove cards and restore events preserve dynamic labels',()=>{
 const first={...incident({}),id:'first',created_at:'2026-11-14'},second={...incident({}),id:'second',created_at:'2026-11-15'};
 const removed={...first,record:{queue_deleted:{active:true}}};assert.deepEqual(updateIncidentQueue([first,second],removed),[second]);assert.equal(deriveIncidentCard(removed).deleted,true);
 const restored={...first,record:{queue_deleted:{active:false,restored_at:'now'},reviewed_label:{title:'Restored video',description:'Reviewed description.',tags:[]}}};
 const queue=updateIncidentQueue([second],restored);assert.deepEqual(queue.map(i=>i.id),['second','first']);assert.equal(deriveIncidentCard(queue[1]).title,'Restored video');assert.equal(deriveIncidentCard(queue[1]).deleted,false);
 assert.equal(updateIncidentQueue(queue,restored).length,2);
});
