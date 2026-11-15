"""Explicit, audited incident retention holds shared by local and cloud cleanup."""
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
import shutil
from threading import RLock

from sqlmodel import Session
from .config import settings
from .db import Incident, ArchiveState, ReviewAction, Source, engine, now
from .video import digest

retention_lock = RLock()


def guarded(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with retention_lock:
            return function(*args, **kwargs)
    return locked


def held(incident):
    record = incident.get('record', {}) if isinstance(incident, dict) else incident.record
    return record.get('retention_hold', {}).get('active') is True


def expires_at(incident):
    if held(incident):
        return None
    created = incident['created_at'] if isinstance(incident, dict) else incident.created_at
    return (datetime.fromisoformat(created) + timedelta(hours=settings.retention_hours)).isoformat()


@guarded
def set_hold(incident_id, active, note):
    """Commit the hold and its audit action together; repeated requests are no-ops."""
    with Session(engine) as session:
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise LookupError('Incident not found')
        already_set=held(incident)==active
        if active:
            filename = incident.record.get('files', {}).get('original')
            folder = (settings.data_dir/'incidents'/incident.id).resolve()
            path = (folder/filename).resolve() if filename else None
            available = bool(path and path.parent == folder and path.is_file())
            if available and digest(path) != incident.record.get('original_sha256'):
                raise ValueError('Original hash mismatch; cannot preserve changed evidence')
            archive = session.get(ArchiveState, incident.id)
            archived = bool(archive and archive.status == 'synced' and not archive.media_evicted and archive.media.get('original'))
            if not available and not archived:
                raise ValueError('Original video is unavailable; a retention hold cannot recover expired or evicted footage')
            source=session.get(Source,incident.source_id)
            if source and source.kind=='upload' and source.path and Path(source.path).is_file():
                raw=Path(source.path)
                expected=source.original_sha256 or incident.record.get('source_sha256')
                actual=digest(raw)
                if expected and expected!=actual:
                    raise ValueError('Original upload hash mismatch; cannot retain changed evidence')
                folder.mkdir(parents=True,exist_ok=True)
                target=folder/('source-original'+raw.suffix.lower())
                if target.exists():
                    if digest(target)!=actual:raise ValueError('Retained original upload hash mismatch')
                else:
                    try:
                        with raw.open('rb') as src,target.open('xb') as dest:shutil.copyfileobj(src,dest)
                        if digest(target)!=actual:raise ValueError('Original upload copy verification failed')
                        target.chmod(0o444)
                    except BaseException:
                        target.unlink(missing_ok=True)
                        raise
                files={**incident.record.get('files',{}),'source_original':target.name}
                incident.record={**incident.record,'files':files,'source_sha256':actual}
        if already_set:
            session.add(incident);session.commit();session.refresh(incident);session.expunge(incident)
            return incident
        stamp = now()
        previous = incident.record.get('retention_hold', {})
        hold = {**previous, 'active': active, 'updated_at': stamp, 'note': note,
            'actor': 'workspace reviewer', 'held_at' if active else 'released_at': stamp}
        incident.record = {**incident.record, 'retention_hold': hold}
        session.add(incident)
        session.add(ReviewAction(incident_id=incident.id,
            decision='retention_held' if active else 'retention_released', note=note))
        session.commit()
        session.refresh(incident)
        session.expunge(incident)
        return incident
