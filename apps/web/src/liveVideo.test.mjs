import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('./liveVideo.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}});
const {publishLiveVideo,receiveLiveVideo,hasDecodedVideo}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));

function browser(){
  const sockets=[],peers=[],timers=new Map();let timer=0;
  globalThis.location={protocol:'https:',host:'safesc.test'};
  globalThis.window={setInterval:fn=>{timers.set(++timer,fn);return timer},setTimeout:fn=>{timers.set(++timer,fn);return timer}};
  const originalClearInterval=globalThis.clearInterval,originalClearTimeout=globalThis.clearTimeout;
  globalThis.clearInterval=globalThis.clearTimeout=id=>timers.delete(id);
  globalThis.WebSocket=class{
    static OPEN=1;readyState=1;sent=[];
    constructor(url){this.url=url;sockets.push(this)}
    send(raw){this.sent.push(JSON.parse(raw))}
    close(){this.closed=true;this.onclose?.()}
    open(){this.onopen?.()}
    async message(value){this.onmessage({data:JSON.stringify(value)});await new Promise(resolve=>setImmediate(resolve));}
  };
  globalThis.RTCPeerConnection=class{
    candidates=[];connectionState='new';senders=[];
    constructor(config){this.config=config;peers.push(this)}
    addTrack(track){this.senders.push({track,getParameters:()=>({encodings:[{}]}),setParameters:async params=>{this.params=params}})}
    getSenders(){return this.senders}
    createOffer(){return Promise.resolve({type:'offer',sdp:'offer'})}
    createAnswer(){return Promise.resolve({type:'answer',sdp:'answer'})}
    setLocalDescription(d){this.localDescription=d;return Promise.resolve()}
    setRemoteDescription(d){this.remoteDescription=d;return Promise.resolve()}
    addIceCandidate(c){assert.ok(this.remoteDescription,'ICE must wait for remote SDP');this.candidates.push(c);return Promise.resolve()}
    close(){this.closed=true}
  };
  return {sockets,peers,timers,restore(){globalThis.clearInterval=originalClearInterval;globalThis.clearTimeout=originalClearTimeout}};
}

test('publisher offers camera video independently of JPEG ACKs and cleans up without stopping camera',async()=>{
  const b=browser();try{
    let stopped=false;const track={kind:'video',stop(){stopped=true}};
    const dispose=publishLiveVideo({getVideoTracks:()=>[track]},'paired-token',()=>{}),ws=b.sockets[0];
    assert.equal(ws.url,'wss://safesc.test/ws/live');ws.open();assert.deepEqual(ws.sent[0],{role:'capture',token:'paired-token'});
    await ws.message({type:'ready',ice_servers:[{urls:'stun:example.test'}],ice_transport_policy:'relay',turn_configured:true});
    await ws.message({type:'viewer_joined',peer_id:'reviewer'});
    assert.equal(b.peers[0].config.iceServers[0].urls,'stun:example.test');
    assert.equal(b.peers[0].config.iceTransportPolicy,'relay');
    assert.deepEqual(b.peers[0].params,{encodings:[{maxBitrate:16000000,maxFramerate:60}],degradationPreference:'maintain-resolution'});
    assert.equal(ws.sent.at(-1).type,'offer');assert.equal(ws.sent.at(-1).peer_id,'reviewer');
    await ws.message({type:'ping'});assert.equal(ws.sent.at(-1).type,'pong');
    dispose();assert.equal(b.peers[0].closed,true);assert.equal(b.timers.size,0);assert.equal(stopped,false);
  }finally{b.restore()}
});

test('viewer queues early ICE until offer, returns answer, and resets on publisher departure',async()=>{
  const b=browser();try{
    const tracks=[];const dispose=receiveLiveVideo('source',()=>{},s=>tracks.push(s),()=>{}),ws=b.sockets[0];ws.open();
    assert.deepEqual(ws.sent[0],{role:'viewer',source_id:'source'});
    await ws.message({type:'ready',ice_servers:[]});
    await ws.message({type:'candidate',peer_id:'phone',candidate:{candidate:'early'}});
    assert.deepEqual(b.peers[0].candidates,[]);
    await ws.message({type:'offer',peer_id:'phone',description:{type:'offer',sdp:'sdp'}});
    assert.equal(b.peers[0].candidates[0].candidate,'early');assert.equal(ws.sent.at(-1).type,'answer');
    await ws.message({type:'peer_left',peer_id:'phone'});assert.equal(b.peers[0].closed,true);assert.equal(tracks.at(-1),null);
    dispose();assert.equal(b.timers.size,0);
  }finally{b.restore()}
});

// Safari can fire playing as soon as a remote track is attached, before RTP arrives.
test('direct preview waits for decoded frames and usable video dimensions',()=>{
  const picture={videoWidth:1920,videoHeight:1080,readyState:2};
  assert.equal(hasDecodedVideo({fps:0,width:1920,height:1080},picture),false);
  assert.equal(hasDecodedVideo({fps:60,width:0,height:0},picture),false);
  assert.equal(hasDecodedVideo({fps:60,width:1920,height:1080},{...picture,videoWidth:0}),false);
  assert.equal(hasDecodedVideo({fps:60,width:1920,height:1080},{...picture,readyState:1}),false);
  assert.equal(hasDecodedVideo({fps:60,width:1920,height:1080},picture),true);
});

test('diagnostics never reach older signaling servers without capability opt-in',async()=>{
  const b=browser();try{
    const dispose=receiveLiveVideo('source',()=>{},()=>{},()=>{}),ws=b.sockets[0];ws.open();
    await ws.message({type:'ready',ice_servers:[]});
    await ws.message({type:'candidate',peer_id:'phone',candidate:{candidate:'early'}});
    const pc=b.peers[0];pc.iceConnectionState='checking';pc.iceGatheringState='gathering';pc.signalingState='stable';
    pc.oniceconnectionstatechange();assert.equal(ws.sent.some(m=>m.type==='diagnostics'),false);
    await ws.message({type:'ready',ice_servers:[],diagnostics_supported:true});
    pc.oniceconnectionstatechange();assert.equal(ws.sent.at(-1).type,'diagnostics');assert.equal(ws.sent.at(-1).remote_peer_id,'phone');
    dispose();
  }finally{b.restore()}
});

test('live metrics classify selected relay route without exposing addresses',async()=>{
  const b=browser();try{
    const metrics=[];const dispose=receiveLiveVideo('source',()=>{},()=>{},m=>metrics.push(m)),ws=b.sockets[0];ws.open();
    await ws.message({type:'ready',ice_servers:[]});
    await ws.message({type:'offer',peer_id:'phone',description:{type:'offer',sdp:'sdp'}});
    const local={candidateType:'host',protocol:'udp',address:'private-address'},remote={candidateType:'relay',protocol:'udp',address:'relay-address'};
    b.peers[0].getStats=async()=>new Map([
      ['video',{type:'inbound-rtp',kind:'video',transportId:'transport',timestamp:1000,framesDecoded:60,bytesReceived:1000,frameWidth:1920,frameHeight:1080}],
      ['transport',{selectedCandidatePairId:'pair'}],['pair',{localCandidateId:'local',remoteCandidateId:'remote'}],['local',local],['remote',remote]
    ]);
    const poll=[...b.timers.values()][0];await poll();
    assert.equal(metrics.at(-1).route,'relay');assert.equal(metrics.at(-1).transport,'udp');assert.equal(JSON.stringify(metrics).includes('address'),false);
    remote.candidateType='host';local.protocol='unexpected';await poll();assert.equal(metrics.at(-1).route,'direct');assert.equal(metrics.at(-1).transport,undefined);
    dispose();
  }finally{b.restore()}
});
