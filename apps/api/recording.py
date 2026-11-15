"""Bounded native camera evidence spool, independent of JPEG model samples.

MediaRecorder timeslices are a continuous container, not standalone videos. Feed
one demuxer in sequence and stream-copy keyframe-aligned, standalone segments.
Only immutable incident windows survive source cleanup; no continuous feed archive.
"""
import asyncio
import csv
import hmac
import math
import shutil
import time
import av
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Query

from .config import settings
from .db import Source, get
from .runtime import pairings, sessions

router = APIRouter()
MAX_CHUNK = 12*1024**2
MAX_SPOOL = 256*1024**2
WINDOW_SECONDS = 150
MIME_TYPES = {'video/webm', 'video/mp4', 'video/x-matroska'}


@dataclass
class Recording:
    source_id: str
    started_at: float
    mime: str
    folder: Path
    process: asyncio.subprocess.Process
    log: object
    sequence: int = 0
    closed: bool = False
    error: str | None = None
    updated_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


recordings: dict[str, Recording] = {}
creating = set()


def segments(recording):
    path = recording.folder/'segments.csv'
    if not path.is_file():
        return []
    result = []
    for row in csv.reader(path.read_text().splitlines()):
        if len(row) != 3:
            continue
        try:
            file = Path(row[0])
            if not file.is_absolute():
                file = recording.folder/file
            start, end = float(row[1]), float(row[2])
            if file.parent.resolve() == recording.folder.resolve() and file.is_file() and math.isfinite(start+end) and end > start:
                result.append((file, start, end))
        except (ValueError, OSError):
            continue
    return result


def prune(recording):
    rows = segments(recording)
    if rows:
        cutoff = rows[-1][2]-WINDOW_SECONDS
        for path, start, end in rows:
            if end < cutoff:
                path.unlink(missing_ok=True)
    # Always retain only sealed segments when removing old data. If the open
    # segment alone exceeds the cap, stop recording instead of exhausting disk.
    files = sorted(recording.folder.glob('segment-*.mkv'))
    total = sum(p.stat().st_size for p in files if p.exists())
    sealed = {p for p, _, _ in rows}
    for path in files:
        if total <= MAX_SPOOL:
            break
        if path in sealed:
            size = path.stat().st_size if path.exists() else 0
            path.unlink(missing_ok=True)
            total -= size
    if total > MAX_SPOOL:
        raise ValueError('Native evidence spool exceeds its disk cap')
    # Bound the segment index as well as its media. ffmpeg owns/appends this file;
    # do not rewrite it under that writer. At the cap, reject further capture.
    log = recording.folder/'ffmpeg.log'
    if log.exists() and log.stat().st_size > 1024**2:
        raise ValueError('Native recording demuxer error log exceeded its bound')
    index = recording.folder/'segments.csv'
    if index.exists() and index.stat().st_size > 2*1024**2:
        raise ValueError('Native recording session reached its index limit; reconnect')


def ffmpeg_exe():
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def reset_spool():
    # Called before accepting sources after startup; uncommitted continuous
    # camera context from a dead worker must never survive indefinitely.
    shutil.rmtree(settings.data_dir/'recording-spool', ignore_errors=True)


async def start_recording(source_id, started_at, mime):
    folder = settings.data_dir/'recording-spool'/source_id
    folder.mkdir(parents=True, exist_ok=True)
    # A stale spool after a worker restart cannot be mixed with a new container.
    for path in folder.iterdir():
        if path.is_file():
            path.unlink()
    log = (folder/'ffmpeg.log').open('wb')
    try:
        process = await asyncio.create_subprocess_exec(ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
            '-probesize', '1000000', '-analyzeduration', '1000000', '-i', 'pipe:0',
            '-map', '0:v:0', '-an', '-c:v', 'copy', '-f', 'segment', '-segment_time', '2',
            '-reset_timestamps', '1', '-segment_format', 'matroska', '-segment_list', str((folder/'segments.csv').resolve()),
            '-segment_list_type', 'csv', str((folder/'segment-%08d.mkv').resolve()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=log)
    except BaseException:
        log.close()
        raise
    return Recording(source_id, started_at, mime, folder, process, log)


async def finish(recording):
    if recording.closed and recording.process.returncode is not None:
        return
    recording.closed = True
    if recording.process.stdin:
        recording.process.stdin.close()
    try:
        await asyncio.wait_for(recording.process.wait(), 10)
    except asyncio.TimeoutError:
        recording.process.kill()
        await recording.process.wait()
        recording.error = 'Native evidence demuxer did not finish'
    recording.log.close()
    if recording.process.returncode:
        recording.error = 'Native evidence container could not be decoded'


async def cleanup(source_id):
    recording = recordings.pop(source_id, None)
    if recording:
        async with recording.lock:
            await finish(recording)
            await asyncio.to_thread(shutil.rmtree, recording.folder, True)


async def retention():
    while True:
        await asyncio.sleep(10)
        for source_id, recording in list(recordings.items()):
            idle = time.time()-recording.updated_at
            if idle > WINDOW_SECONDS or (source_id not in sessions and idle > 30):
                await cleanup(source_id)


async def cleanup_all():
    for source_id in list(recordings):
        await cleanup(source_id)


@router.post('/api/sources/{source_id}/recording')
async def recording_chunk(source_id: str, request: Request, sequence: int = Query(ge=0),
                          started_at: float = Query(ge=0), mime: str = 'video/webm', final: bool = False):
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    pairing = pairings.get(token)
    if not pairing or pairing['source_id'] != source_id or pairing['expires'] < time.time():
        raise HTTPException(401, 'A current pairing token for this source is required')
    if not settings.enable_native_recording:
        raise HTTPException(503, 'Native recording is experimental and disabled on this worker')
    source = get(Source, source_id)
    if not source or source.kind not in ('phone', 'webcam'):
        raise HTTPException(404, 'Camera source not found')
    if not math.isfinite(started_at) or mime.split(';')[0].lower() not in MIME_TYPES:
        raise HTTPException(422, 'Expected a finite recorder start time and supported video MIME type')
    if len(recordings) >= 2 and source_id not in recordings:
        raise HTTPException(409, 'Native recording capacity is occupied')
    body = bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk) > MAX_CHUNK:
            raise HTTPException(413, 'Native recording chunk exceeds 12 MiB')
        body.extend(chunk)
    if not body and not final:
        raise HTTPException(400, 'Empty native recording chunk')
    recording = recordings.get(source_id)
    if recording is None:
        if sequence != 0 or final:
            raise HTTPException(409, 'Native recording must begin with sequence zero and container initialization')
        if source_id in creating:
            raise HTTPException(409, 'Native recording initialization is already in progress')
        creating.add(source_id)
        try:
            recording = await start_recording(source_id, started_at, mime)
            recordings[source_id] = recording
        except OSError:
            raise HTTPException(503, 'Native recording requires the backend ffmpeg executable')
        finally:
            creating.discard(source_id)
    async with recording.lock:
        if recording.closed or recording.error:
            raise HTTPException(409, recording.error or 'Native recording is already finalized')
        if sequence != recording.sequence or started_at != recording.started_at or mime != recording.mime:
            raise HTTPException(409, 'Native recording sequence or clock changed; no chunks may be skipped')
        try:
            if body:
                recording.process.stdin.write(body)
                await asyncio.wait_for(recording.process.stdin.drain(), 15)
            recording.sequence += 1
            recording.updated_at = time.time()
            if final:
                await finish(recording)
            prune(recording)
            if recording.error:
                raise ValueError(recording.error)
        except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError, ValueError) as error:
            recording.error = str(error) or 'Native recording transport failed'
            await finish(recording)
            raise HTTPException(409, 'Native recording stopped; JPEG context remains available')
        return {'status': 'finalized' if final else 'accepted', 'next_sequence': recording.sequence,
            'sealed_segments': len(segments(recording)), 'native_video': True}


async def export_clip(source_id, capture_epoch, start, end, target, wait_for_registration=False):
    """Return actual native source interval or None; never manufacture 60 FPS.

    Copy only complete, contiguous, keyframe-bounded windows. They may include
    extra lead-in/out; timestamps reflect the actual retained native interval.
    """
    if capture_epoch is None:
        return None
    deadline = time.monotonic()+12
    recording = recordings.get(source_id)
    # Some browser muxers withhold even initialization until a fragment/keyframe
    # flush. Give the already asynchronous incident task a bounded chance to use
    # that native data, including a recorder stop/final flush.
    while wait_for_registration and recording is None and time.monotonic() < deadline:
        await asyncio.sleep(.1)
        recording = recordings.get(source_id)
    if recording is None:
        return None
    offset = recording.started_at-capture_epoch
    wanted_start, wanted_end = start-offset, end-offset
    selected = None
    while True:
        rows = segments(recording)
        chosen = [r for r in rows if r[2] > wanted_start and r[1] <= wanted_end]
        # The initial source edge can precede the first source JPEG slightly.
        if chosen and chosen[0][1] <= max(0, wanted_start)+.1 and chosen[-1][2] >= wanted_end-.1:
            if all(b[1]-a[2] <= .15 for a, b in zip(chosen, chosen[1:])):
                selected = chosen
                break
        # closed means stdin is closing, not that ffmpeg has flushed its final
        # fragment/index. An incident waiting on the final MediaRecorder blob
        # must wait for process completion before declaring its window missing.
        if (recording.closed and recording.process.returncode is not None) or recording.error or time.monotonic() >= deadline:
            return None
        await asyncio.sleep(.1)
    async with recording.lock:
        temporary = target.parent/'native-extraction'
        temporary.mkdir(exist_ok=False)
        try:
            copied = []
            for i, (path, first, last) in enumerate(selected):
                copy = temporary/f'{i:06d}.mkv'
                await asyncio.to_thread(shutil.copyfile, path, copy)
                copied.append((copy, last-first))
        except (OSError, ValueError):
            await asyncio.to_thread(shutil.rmtree, temporary, True)
            return None
    try:
        listing = temporary/'concat.txt'
        listing.write_text(''.join(f"file '{path.resolve()}'\nduration {duration:.6f}\n" for path, duration in copied))
        def probe_codec():
            with av.open(str(copied[0][0])) as container:
                return container.streams.video[0].codec_context.name
        codec = await asyncio.to_thread(probe_codec)
        # H.264/H.265/AV1/VP9 can be remuxed to MP4 without changing any frame.
        # VP8 needs a frame-preserving H.264 derivative for existing review tools.
        copy_codec = codec in {'h264', 'hevc', 'av1', 'vp9'}
        encoder = ['-c:v', 'copy'] if copy_codec else ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough']
        process = await asyncio.create_subprocess_exec(ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-f', 'concat', '-safe', '0', '-i', str(listing), '-an', *encoder,
            '-movflags', '+faststart', '-n', str(target), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            code = await asyncio.wait_for(process.wait(), 90)
        except asyncio.TimeoutError:
            process.kill(); await process.wait(); code = -1
        if code:
            target.unlink(missing_ok=True)
            return None
        target.chmod(0o444)
        def timing():
            with av.open(str(target)) as container:
                frames = [{'index': i, 'timestamp': float(frame.time or 0)+selected[0][1]+offset}
                    for i, frame in enumerate(container.decode(video=0))]
            elapsed = frames[-1]['timestamp']-frames[0]['timestamp'] if len(frames)>1 else 0
            return frames, (len(frames)-1)/elapsed if elapsed else 0
        timestamps, actual_fps = await asyncio.to_thread(timing)
        if len(timestamps) < 2:
            target.unlink(missing_ok=True)
            return None
        return {'native_frame_timestamps': timestamps, 'measured_fps': round(actual_fps, 3), 'frames': len(timestamps), 'kind': 'native_camera_recording', 'source_start': selected[0][1]+offset,
            'source_end': selected[-1][2]+offset, 'recorder_started_at': recording.started_at,
            'capture_epoch': capture_epoch, 'mime': recording.mime, 'codec': codec,
            'frame_policy': 'All captured native frames retained; no frame duplication or frame-rate conversion',
            'container_operation': 'codec-copy remux' if copy_codec else 'native frame-preserving H.264 encoding',
            'window_policy': 'Complete keyframe-aligned segments; may include extra original context',
            'segments': len(selected)}
    except (OSError, asyncio.TimeoutError, av.error.FFmpegError):
        target.unlink(missing_ok=True)
        return None
    finally:
        await asyncio.to_thread(shutil.rmtree, temporary, True)
