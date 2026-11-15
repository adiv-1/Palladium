import asyncio
import io
import time

import av
import httpx
import numpy as np
import pytest

from apps.api.config import settings
from apps.api.db import Source, init, save
from apps.api.main import app
from apps.api.runtime import pairings
from apps.api import recording


@pytest.fixture(autouse=True)
def enabled_native(monkeypatch):
    monkeypatch.setattr(settings,'enable_native_recording',True)

def camera():
    init(recover=False)
    source = save(Source(name='Native test camera', kind='phone'))
    token = 'native-'+source.id
    pairings[token] = {'source_id': source.id, 'expires': time.time()+60}
    return source.id, token


def encoded_mp4(fps=60, seconds=6, mime='video/mp4'):
    out = io.BytesIO()
    webm = mime == 'video/webm'
    with av.open(out, 'w', format='webm' if webm else 'mp4', options={} if webm else {'movflags': 'frag_keyframe+empty_moov+default_base_moof'}) as container:
        stream = container.add_stream('libvpx' if webm else 'libx264', rate=fps)
        stream.width = 160; stream.height = 96; stream.pix_fmt = 'yuv420p'
        stream.options = {'deadline': 'realtime', 'cpu-used': '8', 'g': str(fps)} if webm else {'preset': 'ultrafast', 'tune': 'zerolatency', 'g': str(fps), 'keyint_min': str(fps), 'sc_threshold': '0'}
        for i in range(fps*seconds):
            image = np.full((96, 160, 3), i%255, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format='rgb24')
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    return out.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('fps,mime', [(60, 'video/mp4'), (50, 'video/mp4'), (60, 'video/webm')])
async def test_native_continuous_chunks_preserve_real_60fps_and_clock(tmp_path, fps, mime, monkeypatch):
    original_finish = recording.finish
    async def delayed_finish(item):
        if item.process.returncode is None:
            item.closed = True
            await asyncio.sleep(.25)  # stdin closed is not equivalent to muxer flushed
            item.closed = False
        await original_finish(item)
    monkeypatch.setattr(recording, 'finish', delayed_finish)
    source, token = camera()
    transport = httpx.ASGITransport(app=app)
    raw = encoded_mp4(fps=fps, mime=mime)
    headers = {'Authorization': 'Bearer '+token}
    path = tmp_path/'original.mp4'
    waiting = asyncio.create_task(recording.export_clip(source, 100.2, .8, 5.5, path, wait_for_registration=True))
    await asyncio.sleep(.02)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        seq = 0
        # Arbitrary byte boundaries deliberately split MP4 boxes and initialization.
        for offset in range(0, len(raw), 1500):
            response = await client.post(f'/api/sources/{source}/recording', headers=headers,
                params={'sequence': seq, 'started_at': 100, 'mime': mime}, content=raw[offset:offset+1500])
            assert response.status_code == 200, response.text
            seq += 1
        response = await client.post(f'/api/sources/{source}/recording', headers=headers,
            params={'sequence': seq, 'started_at': 100, 'mime': mime, 'final': 'true'}, content=b'')
        assert response.status_code == 200, response.text
        result = await waiting
        assert result is not None
        assert result['source_start'] <= .8 and result['source_end'] >= 5.5
        assert abs(result['measured_fps']-fps) < .2
        assert result['container_operation'] == ('codec-copy remux' if mime == 'video/mp4' else 'native frame-preserving H.264 encoding')
        assert result['native_frame_timestamps'][0]['timestamp'] == pytest.approx(result['source_start'], abs=.03)
        with av.open(str(path)) as container:
            stamps = [float(f.time) for f in container.decode(video=0)]
        assert len(stamps) >= fps*2
        assert all(b > a for a, b in zip(stamps, stamps[1:]))
        assert all(abs((b-a)-1/fps) < .001 for a, b in zip(stamps, stamps[1:]))
        assert await recording.export_clip(source, 100.2, 30, 40, tmp_path/'missing.mp4') is None
    folder = recording.recordings[source].folder
    await recording.cleanup(source)
    assert not folder.exists() and source not in recording.recordings


@pytest.mark.asyncio
async def test_native_upload_is_scoped_ordered_and_bounded(monkeypatch):
    source, token = camera()
    other, other_token = camera()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        url = f'/api/sources/{source}/recording'
        params = {'sequence': 0, 'started_at': 100, 'mime': 'video/mp4'}
        assert (await client.post(url, params=params, content=b'video')).status_code == 401
        assert (await client.post(url, params=params, headers={'Authorization': 'Bearer '+other_token}, content=b'video')).status_code == 401
        headers = {'Authorization': 'Bearer '+token}
        assert (await client.post(url, params={**params, 'sequence': 1}, headers=headers, content=b'video')).status_code == 409
        assert (await client.post(url, params=params, headers=headers, content=b'')).status_code == 400
        monkeypatch.setattr(recording, 'MAX_CHUNK', 10)
        assert (await client.post(url, params=params, headers=headers, content=b'01234567890')).status_code == 413
        assert source not in recording.recordings


def test_native_prune_deletes_old_context_and_bounds_disk(tmp_path, monkeypatch):
    from types import SimpleNamespace
    rows = []
    for i in range(5):
        path = tmp_path/f'segment-{i:08}.mkv'; path.write_bytes(b'x'*50)
        rows.append(f'{path},{i*50},{(i+1)*50}\n')
    (tmp_path/'segments.csv').write_text(''.join(rows))
    monkeypatch.setattr(recording, 'MAX_SPOOL', 120)
    recording.prune(SimpleNamespace(folder=tmp_path))
    assert not (tmp_path/'segment-00000000.mkv').exists()
    assert sum(p.stat().st_size for p in tmp_path.glob('*.mkv')) <= 120


@pytest.mark.asyncio
async def test_disabled_native_recorder_rejects_even_valid_pairing(monkeypatch):
    source_id,token=camera();monkeypatch.setattr(settings,'enable_native_recording',False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post(f'/api/sources/{source_id}/recording?sequence=0&started_at=0&mime=video/mp4',headers={'Authorization':'Bearer '+token},content=b'not-started')
    assert response.status_code==503
    assert source_id not in recording.recordings
