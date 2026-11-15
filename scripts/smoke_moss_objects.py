"""Validate real MOSS localization on a public static image; no detector fixtures."""
import asyncio,json,time
from pathlib import Path
import cv2,httpx
from apps.api.config import settings
from apps.api.video import Frame,encode
from ml.adapters import MossDetector

async def main():
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+settings.app_token},timeout=10) as client:
        r=await client.get('http://127.0.0.1:8000/api/health');r.raise_for_status()
        if r.json()['active_sources']:raise RuntimeError('Do not run the native probe during an active source')
    # Reuse the API-managed native process; do not spawn a second MOSS server.
    settings.moss_service_command=''
    detector=MossDetector(settings.moss_ws_url);detector.use_verifier=True;detector.spatial_enabled=True
    image=cv2.imread('data/smoke/bus.jpg')
    if image is None:raise RuntimeError('The public-image smoke fixture is missing')
    raw=encode(cv2.resize(image,(480,640)));started=time.monotonic()
    try:
        for i in range(24):
            await detector.observe([Frame(i*.5,i,raw)],[i*.5],[])
            await asyncio.sleep(.5)
        await asyncio.sleep(16)
        result={'accepted':bool(detector.object_sample),'input':'Repeated public Ultralytics bus image with source-frame margins',
            'physical_phone_tested':False,'model':'MOSS-VL-Realtime',
            'sample':{k:v for k,v in (detector.object_sample or {}).items() if k!='jpeg'},
            'raw':detector.observer_text,'runtime_seconds':time.monotonic()-started}
        Path('data/moss-object-validation.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
        if not result['accepted']:raise RuntimeError('MOSS returned no validated frame-aligned spatial output')
    finally:await detector.close()

if __name__=='__main__':asyncio.run(main())
