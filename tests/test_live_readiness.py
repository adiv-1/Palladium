from types import SimpleNamespace

from apps.api import jobs
from apps.api.config import settings


def test_next_recording_keeps_live_models_warm_before_gpu_background_work(monkeypatch):
    monkeypatch.setattr(jobs, 'sessions', {})
    monkeypatch.setattr(jobs, 'live_warmup', False)
    monkeypatch.setattr(jobs, 'live_idle_until', 0)
    monkeypatch.setattr(jobs.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(settings, 'live_idle_grace_seconds', 60)
    queued = SimpleNamespace(kind='review_brief', status='queued')
    assert jobs.background_ready(queued)
    jobs.preserve_live_readiness()
    assert not jobs.background_ready(queued, at=100)
    assert not jobs.background_ready(queued, at=159.9)
    assert jobs.background_ready(queued, at=160)
    assert queued.status == 'queued'  # Deferral never cancels or loses the work.
    monkeypatch.setattr(jobs.time, 'monotonic', lambda: 140)
    jobs.preserve_live_readiness()
    assert not jobs.background_ready(queued, at=199.9)
    assert jobs.background_ready(queued, at=200)


def test_cpu_exports_continue_and_live_sessions_still_exclude_gpu_jobs(monkeypatch):
    monkeypatch.setattr(jobs, 'sessions', {'phone': object()})
    monkeypatch.setattr(jobs, 'live_warmup', True)
    monkeypatch.setattr(jobs, 'live_idle_until', 1000)
    assert jobs.background_ready(SimpleNamespace(kind='relift_export'), at=0)
    assert not jobs.background_ready(SimpleNamespace(kind='learning_train'), at=2000)
    assert not jobs.background_ready(SimpleNamespace(kind='relayvsr'), at=2000)


def test_grace_period_can_be_disabled_without_altering_model_settings(monkeypatch):
    monkeypatch.setattr(jobs, 'sessions', {})
    monkeypatch.setattr(jobs, 'live_warmup', False)
    monkeypatch.setattr(jobs, 'live_idle_until', 0)
    monkeypatch.setattr(jobs.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(settings, 'live_idle_grace_seconds', 0)
    jobs.preserve_live_readiness()
    assert jobs.background_ready(SimpleNamespace(kind='review_brief'))


def test_model_warmup_owns_gpu_without_starting_background_jobs(monkeypatch):
    monkeypatch.setattr(jobs, 'sessions', {})
    monkeypatch.setattr(jobs, 'gpu_busy', True)
    monkeypatch.setattr(jobs, 'live_warmup', False)
    monkeypatch.setattr(jobs, 'live_idle_until', 0)
    assert not jobs.background_ready(SimpleNamespace(kind='review_brief'), at=1000)
    assert jobs.background_ready(SimpleNamespace(kind='relift_export'), at=1000)


async def test_closing_live_source_reserves_gpu_before_unregistering(monkeypatch):
    from apps.api import runtime
    from apps.api.db import Source, init, save
    init(recover=False)
    monkeypatch.setattr(runtime, 'sessions', {})
    session = runtime.VideoSession(save(Source(name='Live readiness close test', kind='phone')))
    runtime.sessions[session.source.id] = session
    monkeypatch.setattr(settings, 'streaming_model', 'moss')
    calls=[]
    def reserve():
        assert session.source.id in runtime.sessions
        calls.append(True)
    monkeypatch.setattr(jobs, 'preserve_live_readiness', reserve)
    await session.close()
    assert calls == [True] and session.source.id not in runtime.sessions


async def test_failed_warmup_releases_gpu_ownership(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from apps.api import main as api
    from ml.service import model_service
    monkeypatch.setattr(jobs, 'sessions', {})
    monkeypatch.setattr(jobs, 'gpu_busy', False)
    monkeypatch.setattr(jobs, 'live_warmup', False)
    monkeypatch.setattr(api, 'busy', lambda: False)
    async def fail():
        assert jobs.gpu_busy and jobs.live_warmup
        assert not jobs.background_ready(SimpleNamespace(kind='review_brief'))
        raise RuntimeError('Warmup test failure')
    monkeypatch.setattr(model_service, 'ensure', fail)
    with pytest.raises(HTTPException) as error:
        await api.warm_moss()
    assert error.value.status_code == 503
    assert not jobs.gpu_busy and not jobs.live_warmup


async def test_cpu_export_does_not_clear_a_concurrent_live_warmup_gpu_owner(monkeypatch):
    import asyncio
    monkeypatch.setattr(jobs, 'sessions', {})
    monkeypatch.setattr(jobs, 'gpu_busy', True)
    monkeypatch.setattr(jobs, 'live_warmup', True)
    job=SimpleNamespace(kind='relift_export',status='queued',priority=0,id='cpu',attempts=0)
    job.model_dump=lambda: {'id':job.id,'status':job.status}
    monkeypatch.setattr(jobs, 'rows', lambda cls: [job])
    monkeypatch.setattr(jobs, 'save', lambda value: value)
    completed=asyncio.Event()
    async def publish(*args):pass
    async def export(value):
        assert jobs.gpu_busy
        completed.set()
        return {'status':'ok'}
    monkeypatch.setattr(jobs.hub, 'publish', publish)
    monkeypatch.setattr(jobs, 'run', export)
    task=asyncio.create_task(jobs.worker())
    try:
        await asyncio.wait_for(completed.wait(),1)
        assert job.status=='completed' and jobs.gpu_busy
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
