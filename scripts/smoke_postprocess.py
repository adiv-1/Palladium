"""Run GPU postprocessors sequentially, preserving outputs, logs and measured memory."""
import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def run(name,argv,cwd,outputs):
    folder=ROOT/'data/smoke';result_path=folder/f'{name}-metrics.json'
    peak=[0];done=threading.Event()
    def monitor():
        while not done.is_set():
            try:
                value=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True,timeout=4)
                peak[0]=max(peak[0],float(value.strip()))
            except Exception:pass
            done.wait(.5)
    watcher=threading.Thread(target=monitor,daemon=True);watcher.start();started=time.monotonic()
    with (folder/f'{name}-inference.log').open('wb') as log:
        try:
            process=subprocess.run(list(map(str,argv)),cwd=cwd,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
            code=process.returncode
        except subprocess.TimeoutExpired:code=-1
    done.set();watcher.join(6)
    result={'model':name,'status':'passed' if code==0 else 'failed','exit_code':code,
        'runtime_seconds':round(time.monotonic()-started,3),'sampled_device_peak_vram_mb':peak[0],
        'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=cwd,text=True).strip(),
        'outputs':{str(p.relative_to(ROOT)):sha(p) for p in outputs if p.is_file()},
        'note':'Actual inference on the official upstream demo; device memory sampled at 0.5-second intervals.'}
    result_path.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    return code

def main():
    relay=ROOT/'third_party/RelayVSR'; relift=ROOT/'third_party/ReLift';folder=ROOT/'data/smoke'
    output=folder/'relay-output.mp4'
    run('relayvsr',[relay/'.venv/bin/python','inference.py','--input',folder/'relay-input.mp4','--output',output],relay,[output])
    target=folder/'relift-output'
    run('relift',[relift/'.venv/bin/python','scripts/demo_video.py',relift/'assets/demo/cartwheel.mp4','--out',target,'--num_frames','84'],relift,[target/'tracks.npz',target/'tracks.mp4'])

if __name__=='__main__':main()
