import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const {outputText}=ts.transpileModule(await readFile(new URL('./nativeRecording.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}});
const {recordEvidence}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));

test('native fragments upload in order, final blob drains before finish, camera ownership retained',async()=>{
  const calls=[],messages=[],savedFetch=globalThis.fetch,savedRecorder=globalThis.MediaRecorder;let native;
  globalThis.MediaRecorder=class{
    static isTypeSupported(type){return type==='video/mp4'}
    state='inactive';mimeType='video/mp4';
    constructor(stream,options){native=this;this.stream=stream;assert.equal(options.videoBitsPerSecond,10000000)}
    start(slice){assert.equal(slice,1000);this.state='recording'}
    stop(){this.state='inactive';this.ondataavailable({data:new Blob(['tail'])});this.onstop()}
  };
  globalThis.fetch=async(url,options)=>{calls.push({url,body:await options.body.text(),auth:options.headers.Authorization});return {ok:true,json:async()=>({sealed_segments:1})}};
  let trackStopped=false;
  try{
    const handle=recordEvidence({getTracks:()=>[{stop(){trackStopped=true}}]},'source','pairing',m=>messages.push(m));
    native.ondataavailable({data:new Blob(['init'])});native.ondataavailable({data:new Blob(['middle'])});
    await handle.stop();
    assert.deepEqual(calls.map(c=>c.body),['init','middle','tail','']);
    assert.deepEqual(calls.map(c=>new URL(c.url,'https://test').searchParams.get('sequence')),['0','1','2','3']);
    assert.equal(new URL(calls.at(-1).url,'https://test').searchParams.get('final'),'true');
    assert.ok(calls.every(c=>c.auth==='Bearer pairing'));assert.equal(trackStopped,false);
    assert.match(messages.at(-1),/saved/);
  }finally{globalThis.fetch=savedFetch;globalThis.MediaRecorder=savedRecorder}
});

test('upload failure stops native recorder explicitly rather than discarding a container fragment',async()=>{
  const savedFetch=globalThis.fetch,savedRecorder=globalThis.MediaRecorder;let native;const messages=[];
  globalThis.MediaRecorder=class{
    static isTypeSupported(){return true}state='inactive';mimeType='video/mp4';
    constructor(){native=this}start(){this.state='recording'}stop(){this.state='inactive';this.onstop()}
  };
  globalThis.fetch=async()=>({ok:false,status:413,json:async()=>({detail:'Oversized fragment'})});
  try{
    const handle=recordEvidence({},'source','pairing',m=>messages.push(m));native.ondataavailable({data:new Blob(['init'])});
    await new Promise(r=>setImmediate(r));await handle.stop();
    assert.equal(native.state,'inactive');assert.match(messages.at(-1),/interrupted.*Oversized fragment/);
  }finally{globalThis.fetch=savedFetch;globalThis.MediaRecorder=savedRecorder}
});

test('recording uses an unhinted clone without changing or stopping the live camera',async()=>{
  const saved={fetch:globalThis.fetch,recorder:globalThis.MediaRecorder,stream:globalThis.MediaStream};let clonedStopped=false,originalStopped=false,received,streamClones=0;
  const clone={contentHint:'motion',stop(){clonedStopped=true}},original={contentHint:'motion',clone(){return clone},stop(){originalStopped=true}};
  globalThis.MediaStream=class{constructor(tracks){this.tracks=tracks}getVideoTracks(){return this.tracks}};
  globalThis.MediaRecorder=class{static isTypeSupported(){return true}state='inactive';mimeType='video/mp4';constructor(stream){received=stream}start(){this.state='recording'}stop(){this.state='inactive';this.onstop()}};
  globalThis.fetch=async()=>({ok:true,json:async()=>({})});
  try{const handle=recordEvidence({getVideoTracks:()=>[original],clone(){streamClones++;return new MediaStream([clone])}},'source','token',()=>{});assert.equal(streamClones,1);assert.equal(received.getVideoTracks()[0],clone);assert.equal(clone.contentHint,'');assert.equal(original.contentHint,'motion');await handle.stop();assert.equal(clonedStopped,true);assert.equal(originalStopped,false)}
  finally{globalThis.fetch=saved.fetch;globalThis.MediaRecorder=saved.recorder;globalThis.MediaStream=saved.stream}
});
