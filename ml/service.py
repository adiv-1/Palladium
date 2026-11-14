"""Lifecycle for an optional locally managed MOSS service, freeing VRAM for background jobs."""
import asyncio
import json
import os
import signal
from pathlib import Path
import httpx
from apps.api.config import settings

class ModelService:
    def __init__(self):
        self.process=None; self.log=None; self.ready=False; self.lock=asyncio.Lock()
    async def ensure(self):
        async with self.lock:
            await self._ensure()
    async def _ensure(self):
        if not settings.moss_service_command: return
        if self.process and self.process.returncode is None and self.ready: return
        await self._stop()
        argv=json.loads(settings.moss_service_command)
        if not isinstance(argv,list) or not all(isinstance(x,str) for x in argv): raise RuntimeError('MOSS_SERVICE_COMMAND must be a JSON argument array')
        self.log=(settings.data_dir/'moss-service.log').open('ab')
        self.process=await asyncio.create_subprocess_exec(*argv,stdout=self.log,stderr=self.log,start_new_session=True)
        health=settings.moss_ws_url.replace('ws://','http://').replace('wss://','https://').split('/v1/')[0]+'/health'
        deadline=asyncio.get_running_loop().time()+180
        async with httpx.AsyncClient(timeout=3) as client:
            while asyncio.get_running_loop().time()<deadline:
                if self.process.returncode is not None:
                    await self._stop()
                    raise RuntimeError('MOSS exited; inspect data/moss-service.log')
                try:
                    r=await client.get(health)
                    if r.is_success and r.json().get('model_loaded'):
                        self.ready=True
                        return
                except (httpx.HTTPError,ValueError): pass
                await asyncio.sleep(1)
        await self._stop();raise RuntimeError('MOSS did not become ready within 180 seconds')
    async def stop(self):
        async with self.lock:
            await self._stop()
    async def _stop(self):
        self.ready=False
        if self.process and self.process.returncode is None:
            try: os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(self.process.wait(),10)
            except asyncio.TimeoutError:
                os.killpg(self.process.pid,signal.SIGKILL);await self.process.wait()
        self.process=None
        if self.log: self.log.close();self.log=None

model_service=ModelService()
