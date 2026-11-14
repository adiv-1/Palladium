"""Configure the tested stack for loopback-only validation; no public exposure."""
import json
import os
from pathlib import Path
from dotenv import dotenv_values

root=Path(__file__).resolve().parents[1]
env=root/'.env'
config=dict(dotenv_values(env))
command=[str(root/'third_party/MOSS-VL/.venv/bin/python'),'-u',str(root/'third_party/MOSS-VL/inference/realtime/run_online_inference.py'),
    '--serve','--host','127.0.0.1','--port','18001','--checkpoint',str(root/'weights/moss-nf4'),'--attention-backend','flash_attention_2']
config.update({
    'PUBLIC_URL':'http://localhost:18000','STREAMING_MODEL':'moss','MOSS_WS_URL':'ws://127.0.0.1:18001/v1/realtime',
    'MOSS_SERVICE_COMMAND':json.dumps(command),'MODEL_REVISION':json.loads((root/'model-lock.json').read_text())['moss']['weights_revision'],
    'ENHANCEMENT_MODEL':'fastllve','FASTLLVE_CHECKPOINT':str(root/'weights/fastllve/FastLLVE_pretrain/FastLLVE_outdoor.pth'),
    'ENABLE_TRACKING':'true','ENABLE_RELAY_VSR':'true','ENABLE_RELIFT':'true',
    'RELIFT_PYTHON':str(root/'third_party/ReLift/.venv/bin/python'),'RELIFT_FRAME_LIMIT':'84',
    'DATA_DIR':'data','SAMPLE_FPS':'1','CAPTURE_FPS':'8'})
with os.fdopen(os.open(env,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:
    stream.write(''.join(f'{key}={json.dumps(value)}\n' for key,value in config.items() if value is not None))
print('Configured tested real models for private loopback validation; public access remains disabled.')
