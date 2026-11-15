import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('./geolocation.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}});
const {shareCameraLocation}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
function fixture(){
  const oldWindow=globalThis.window,oldPerformance=globalThis.performance;let clock=0,timerId=0;
  const timers=new Map(),sent=[],states=[],cleared=[];
  globalThis.performance={now:()=>clock};globalThis.window={setTimeout(fn,delay){timers.set(++timerId,{fn,at:clock+delay});return timerId},clearTimeout(id){timers.delete(id)}};
  const provider={watchPosition(success,error,options){this.success=success;this.error=error;this.options=options;return 4},getCurrentPosition(success,error,options){this.fallbackSuccess=success;this.fallbackError=error;this.fallbackOptions=options},clearWatch(id){cleared.push(id)}};
  return {provider,sent,states,timers,cleared,start(){return shareCameraLocation(m=>sent.push(m),s=>states.push(s),provider)},fix(latitude=34,accuracy=10){provider.success({coords:{latitude,longitude:-118,accuracy},timestamp:1700000000123})},advance(ms){clock+=ms;for(const [id,t] of [...timers])if(t.at<=clock){timers.delete(id);t.fn()}},restore(){globalThis.window=oldWindow;globalThis.performance=oldPerformance}};
}
test('location starts only on explicit helper invocation, opts into high accuracy, and keeps measurement time',()=>{
  const f=fixture();try{
    assert.equal(f.provider.success,undefined);const stop=f.start();assert.deepEqual(f.provider.options,{enableHighAccuracy:true,timeout:30000,maximumAge:5000});
    assert.equal(f.sent.length,0);f.fix();assert.deepEqual(f.sent[0],{type:'geolocation',status:'active',latitude:34,longitude:-118,accuracy_m:10,measured_at:1700000000123});
    stop();assert.deepEqual(f.cleared,[4]);assert.equal(f.sent.at(-1).status,'disabled');
  }finally{f.restore()}
});
test('many GPS fixes coalesce to newest position at most once every five seconds',()=>{
  const f=fixture();try{
    const stop=f.start();f.fix(34);f.advance(1000);f.fix(35);f.fix(36);assert.equal(f.sent.length,1);assert.equal(f.timers.size,1);
    f.advance(3999);assert.equal(f.sent.length,1);f.advance(1);assert.equal(f.sent.length,2);assert.equal(f.sent[1].latitude,36);
    stop();
  }finally{f.restore()}
});
test('turning off immediately clears watch and queued GPS; stale callbacks cannot leak location',()=>{
  const f=fixture();try{
    const stop=f.start();f.fix();f.advance(50);f.fix(35);stop();assert.equal(f.timers.size,0);const count=f.sent.length;
    f.advance(6000);f.fix(36);f.provider.error({code:2});stop();assert.equal(f.sent.length,count);assert.deepEqual(f.cleared,[4]);
    assert.equal(f.sent.at(-1).status,'disabled');
  }finally{f.restore()}
});
test('denied permission stops GPS without throwing or stopping camera work',()=>{
  const f=fixture();try{
    const stop=f.start();assert.doesNotThrow(()=>f.provider.error({code:1}));assert.equal(f.sent.at(-1).status,'denied');assert.deepEqual(f.cleared,[4]);
    const count=f.sent.length;f.fix();assert.equal(f.sent.length,count);assert.match(f.states.at(-1).message,/Camera video continues/);stop();
  }finally{f.restore()}
});
test('unavailable status clears pending coordinates and invalid fixes never reach the server',()=>{
  const f=fixture();try{
    const stop=f.start();f.fix();f.advance(10);f.fix(35);f.provider.error({code:2});assert.equal(f.timers.size,0);assert.deepEqual(f.sent.at(-1),{type:'geolocation',status:'unavailable'});
    f.advance(5000);f.fix(91);assert.notEqual(f.sent.at(-1).status,'active');f.fix(34);assert.equal(f.sent.at(-1).status,'active');stop();
  }finally{f.restore()}
});

test('GPS timeout tries browser approximate positioning and retains its real accuracy',()=>{
 const f=fixture();try{
  const stop=f.start();f.provider.error({code:3});assert.equal(f.provider.fallbackOptions.enableHighAccuracy,false);
  f.provider.fallbackSuccess({coords:{latitude:34.02,longitude:-118.29,accuracy:140},timestamp:1700000000123});
  assert.equal(f.sent.at(-1).status,'active');assert.equal(f.sent.at(-1).accuracy_m,140);stop();
 }finally{f.restore()}
});
test('late approximate fixes cannot restore location after sharing is turned off',()=>{
 const f=fixture();try{
  const stop=f.start();f.provider.error({code:2});stop();const count=f.sent.length;
  f.provider.fallbackSuccess({coords:{latitude:34.02,longitude:-118.29,accuracy:140},timestamp:1700000000123});
  assert.equal(f.sent.length,count);assert.equal(f.sent.at(-1).status,'disabled');
 }finally{f.restore()}
});
