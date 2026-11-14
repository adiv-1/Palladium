"""Ask the real streaming model for a benign schema status, never a fabricated alert."""
import asyncio
import json
import os
import time
from pathlib import Path

# Reuse the API-managed process; this script must not launch a second GPU server.
os.environ['MOSS_SERVICE_COMMAND']=''
from ml.adapters import MossDetector
from apps.api.video import Frame

async def main():
    detector=MossDetector('ws://127.0.0.1:18001/v1/realtime')
    raw=[];results=[];started=time.monotonic()
    image=Path('data/smoke/bus.jpg').read_bytes()
    try:
        for index in range(12):
            await detector.observe([Frame(float(index),index,image)],[float(index)],[])
            if index==4:
                await detector.ws.send(json.dumps({'type':'prompt','text':
                    'Engineering status test on a repeated static image, not an incident. '
                    'Reply now with exactly this valid JSON object, without commentary or markdown: '
                    '{"state":"NORMAL","confidence":0.5,"observable_events":[],"uncertainties":["Static test image"],"supporting_frames":[0,4]}'}))
            await asyncio.sleep(1)
            results.extend(detector.drain())
        for _ in range(40):
            results.extend(detector.drain())
            if results:break
            await asyncio.sleep(1)
        report={'runtime_seconds':time.monotonic()-started,'validated_observations':[d.model_dump() for d in results],
            'error':detector.error,'unparsed_tail':detector.text[-1500:],'test':'Prompted NORMAL status on repeated public image; not an incident-detection benchmark'}
        Path('data/smoke/moss-schema.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
        assert results,'The real model did not produce a schema-valid observation'
        assert all(d.state=='NORMAL' for d in results),'Unexpected positive result on a benign schema test'
    finally:await detector.close()

if __name__=='__main__':asyncio.run(main())
