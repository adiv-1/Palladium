import test from 'node:test';
import assert from 'node:assert/strict';
import {deriveIncidentCard,updateIncidentQueue} from './incidentQueueState.mjs';
const incident=record=>({id:'sample',review_status:'pending',start_time:1,end_time:5,record});
test('pending cards show actual model observations with provenance, without changing records',()=>{
 const input=incident({action:'object_taking',observable_events:[{observation:'A person carries an object.'}],observation_model:{name:'Qwen3-VL'},retention_hold:{active:true}});
 const before=JSON.stringify(input),first=deriveIncidentCard(input);
 assert.equal(first.title,'A person carries an object.');assert.equal(first.assessmentSource,'Visual verifier · initial assessment');assert.equal(first.reviewStatus,'Pending human review');assert.equal(first.held,true);assert.equal(JSON.stringify(input),before);
 input.record.observable_events[0].observation='Two people make physical contact.';
 assert.equal(deriveIncidentCard(input).title,'Two people make physical contact.');
});
test('manual bookmarks without model observations remain neutral',()=>{
 const input=incident({trigger_origin:'reviewer',observable_events:[{observation:'User-requested full review: a filename.mp4'}],clip_review:{report:{takeaway:'Secondary suggestion.'}}});
 assert.equal(deriveIncidentCard(input).title,'Saved video for review');
 assert.equal(deriveIncidentCard(input).assessmentSource,null);
});
test('completed native MOSS analysis takes priority without promoting a human decision',()=>{
 const input=incident({trigger_origin:'reviewer',source_sha256:'original-sha',moss_analysis:{model:{name:'MOSS-VL-Realtime'},source_sha256:'original-sha',status:'completed',kind:'moss_native_full_clip',text:'Two people walk along the sidewalk. No physical contact is visible.'},reviewed_label:{title:'Uploader description',description:'A conflicting description',tags:['context']}});
 const before=JSON.stringify(input),card=deriveIncidentCard(input);
 assert.equal(card.title,'Two people walk along the sidewalk.');assert.equal(card.description,'No physical contact is visible.');assert.equal(card.assessmentSource,'MOSS · full recording analysis');assert.equal(card.labelVisible,false);assert.equal(card.reviewed,true);assert.deepEqual(card.tags,[]);assert.equal(card.reviewStatus,'Pending human review');assert.equal(JSON.stringify(input),before);
 input.record.moss_analysis.source_sha256='different-original';assert.equal(deriveIncidentCard(input).assessmentSource,null);
 input.record.moss_analysis.source_sha256='original-sha';input.record.moss_analysis.status='incomplete';assert.equal(deriveIncidentCard(input).assessmentSource,null);
});
test('native observations and verifier output are never attributed to each other',()=>{
 const input=incident({moss_observations:[{model:'MOSS-VL-Realtime',text:'A person stands nearby.'}],observable_events:[{observation:'A person falls.'}],observation_model:{name:'Qwen3-VL'}});
 const card=deriveIncidentCard(input);assert.equal(card.title,'A person stands nearby.');assert.equal(card.assessmentSource,'MOSS · live observations');
 input.record.moss_observations=[];assert.equal(deriveIncidentCard(input).title,'A person falls.');assert.equal(deriveIncidentCard(input).assessmentSource,'Visual verifier · initial assessment');
});
test('filenames, IDs, uploader context and secondary review never choose model wording',()=>{
 const input=incident({action:'physical_contact',observable_events:[{observation:'A person makes contact.'}]});
 const expected=deriveIncidentCard(input);input.id='another-upload';input.record.source_file='quiet-video.mp4';input.record.reviewer_context={text:'A staged jacket is taken'};input.record.clip_review={report:{takeaway:'A different conclusion'}};
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
 input.review_status='confirmed';const before=JSON.stringify(input),card=deriveIncidentCard(input);assert.equal(card.title,'A jacket is taken');assert.equal(card.description,'A person picks up the jacket and moves away.');assert.deepEqual(card.tags,['jacket','staged']);assert.equal(card.reviewed,true);assert.equal(card.initialObservation,'Incorrect bag description.');assert.equal(JSON.stringify(input),before);
});
test('soft-deletion events remove cards and restore events preserve dynamic labels',()=>{
 const first={...incident({}),id:'first',created_at:'2026-11-14'},second={...incident({}),id:'second',created_at:'2026-11-15'};
 const removed={...first,record:{queue_deleted:{active:true}}};assert.deepEqual(updateIncidentQueue([first,second],removed),[second]);assert.equal(deriveIncidentCard(removed).deleted,true);
 const restored={...first,record:{queue_deleted:{active:false,restored_at:'now'},reviewed_label:{title:'Restored video',description:'Reviewed description.',tags:[]}}};
 const queue=updateIncidentQueue([second],restored);assert.deepEqual(queue.map(i=>i.id),['second','first']);assert.equal(deriveIncidentCard(queue[1]).title,'Restored video');assert.equal(deriveIncidentCard(queue[1]).deleted,false);
 assert.equal(updateIncidentQueue(queue,restored).length,2);
});
