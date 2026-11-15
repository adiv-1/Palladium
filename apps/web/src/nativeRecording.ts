// Native encoded camera evidence is independent of JPEG inference and WebRTC display.
export type RecordingHandle={stop:()=>Promise<void>};
export function recordEvidence(stream:MediaStream,sourceId:string,token:string,onStatus:(message:string)=>void):RecordingHandle{
  if(typeof MediaRecorder==='undefined'){onStatus('Smooth recording unavailable in this browser · sampled evidence only');return {stop:async()=>{}}}
  const mime=['video/mp4;codecs=avc1.420034','video/mp4;codecs=avc1.64002A','video/mp4;codecs=avc1','video/mp4','video/webm;codecs=vp8','video/webm;codecs=vp9','video/webm'].find(type=>MediaRecorder.isTypeSupported(type));
  if(!mime){onStatus('Native recording format unavailable · sampled evidence only');return {stop:async()=>{}}}
  // Give recording its own tracks and hints without mutating live capture.
  const recordingStream=typeof stream.clone==='function'?stream.clone():typeof MediaStream!=='undefined'&&typeof stream.getVideoTracks==='function'?new MediaStream(stream.getVideoTracks().map(track=>track.clone())):stream;
  const copies=recordingStream!==stream?recordingStream.getVideoTracks():[];
  copies.forEach(track=>{track.contentHint=''});
  const release=()=>copies.forEach(track=>track.stop());
  let recorder:MediaRecorder;
  try{recorder=new MediaRecorder(recordingStream,{mimeType:mime,videoBitsPerSecond:10_000_000,videoKeyFrameIntervalDuration:1000} as MediaRecorderOptions)}
  catch{release();onStatus('Native recorder could not start · sampled evidence only');return {stop:async()=>{}}}
  const startedAt=performance.now()/1000,actualMime=recorder.mimeType||mime;
  let chain=Promise.resolve(),pending=0,sequence=0,failed=false,ending=false;
  let resolveStopped:()=>void;const stopped=new Promise<void>(resolve=>{resolveStopped=resolve});
  const endpoint=(final=false)=>`/api/sources/${encodeURIComponent(sourceId)}/recording?${new URLSearchParams({sequence:String(sequence++),started_at:String(startedAt),mime:actualMime,...(final?{final:'true'}:{})})}`;
  async function upload(blob:Blob,final=false){
    const response=await fetch(endpoint(final),{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/octet-stream'},body:blob,signal:AbortSignal.timeout(15000)});
    if(!response.ok){let detail=`HTTP ${response.status}`;try{detail=(await response.json()).detail||detail}catch{}throw Error(typeof detail==='string'?detail:'Upload rejected')}
    if(!final){const state=await response.json().catch(()=>({}));onStatus(state.sealed_segments>0?'Smooth camera recording active':'Buffering native camera recording…')}
  }
  function fail(error:unknown){if(failed)return;failed=true;onStatus(`Smooth recording interrupted · ${error instanceof Error?error.message:'upload failed'} · sampled evidence remains available`);if(recorder.state!=='inactive')recorder.stop();release()}
  recorder.ondataavailable=event=>{
    if(!event.data.size||failed)return;
    // Dropping a container fragment corrupts all following fragments. Stop explicitly.
    if(event.data.size>12*1024*1024||pending>=4){fail(Error('Recording upload cannot keep up with this connection'));return}
    pending++;
    chain=chain.then(async()=>{try{if(!failed)await upload(event.data)}finally{pending--}}).catch(fail);
  };
  recorder.onerror=event=>fail((event as Event&{error?:Error}).error||Error('Recorder failed'));
  recorder.onstop=()=>{
    release();
    chain=chain.then(async()=>{if(!failed){await upload(new Blob([]),true);onStatus('Native camera recording saved')}}).catch(fail).finally(()=>resolveStopped());
  };
  try{recorder.start(1000);onStatus('Preparing native camera recording…')}
  catch(error){fail(error);release();resolveStopped!()}
  return {stop:async()=>{if(!ending){ending=true;if(recorder.state!=='inactive')recorder.stop();release()}await stopped}};
}
