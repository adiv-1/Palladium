import test from 'node:test';
import assert from 'node:assert/strict';
import {deriveIncidentCard} from './incidentQueueState.mjs';
const incident=record=>({id:'sample',review_status:'pending',start_time:1,end_time:5,record});
test('automatic card title and observation follow the actual model record',()=>{
 const input=incident({action:'object_taking',observable_events:[{observation:'A person carries an object.'}],retention_hold:{active:true}});
 const before=JSON.stringify(input),first=deriveIncidentCard(input);
 assert.equal(first.title,'Object-taking flagged');assert.equal(first.initialObservation,'A person carries an object.');assert.equal(first.held,true);assert.equal(JSON.stringify(input),before);
 input.record.action='physical_contact';input.record.observable_events[0].observation='Two people make physical contact.';
 const second=deriveIncidentCard(input);assert.equal(second.title,'Physical contact flagged');assert.equal(second.initialObservation,'Two people make physical contact.');
});
test('manual card title uses its original request rather than a secondary review',()=>{
 const input=incident({trigger_origin:'reviewer',observable_events:[{observation:'A reviewer saved this moment.'}],clip_review:{report:{takeaway:'Secondary suggestion.'}}});
 assert.equal(deriveIncidentCard(input).title,'A reviewer saved this moment.');
 input.record.observable_events[0].observation='A different requested moment.';
 assert.equal(deriveIncidentCard(input).title,'A different requested moment.');
 assert.equal(deriveIncidentCard(incident({trigger_origin:'reviewer'})).title,'Requested clip review');
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
