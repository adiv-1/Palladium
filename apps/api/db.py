from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, JSON, event
from sqlmodel import SQLModel, Field, Session, create_engine, select
from .config import settings

def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return str(uuid4())

class Source(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    name: str
    kind: str
    status: str = 'waiting'
    created_at: str = Field(default_factory=now)
    path: str | None = None
    original_sha256: str | None = None
    telemetry: dict = Field(default_factory=dict, sa_column=Column(JSON))

class Incident(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    source_id: str = Field(index=True)
    created_at: str = Field(default_factory=now)
    start_time: float
    end_time: float
    severity: str
    confidence: float
    review_status: str = 'pending'
    simulated: bool = False
    record: dict = Field(default_factory=dict, sa_column=Column(JSON))

class Job(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    incident_id: str = Field(index=True)
    kind: str
    priority: int = 2
    status: str = 'queued'
    attempts: int = 0
    error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))

class ReviewAction(SQLModel, table=True):
    id: str = Field(default_factory=uid, primary_key=True)
    incident_id: str = Field(index=True)
    timestamp: str = Field(default_factory=now)
    decision: str
    note: str

class ArchiveState(SQLModel, table=True):
    incident_id: str = Field(primary_key=True)
    status: str = 'pending'
    media_evicted: bool = False
    fingerprint: str = ''
    manifest_key: str = ''
    media: dict = Field(default_factory=dict, sa_column=Column(JSON))
    bytes_stored: int = 0
    error: str | None = None
    updated_at: str = Field(default_factory=now)

engine = create_engine(f'sqlite:///{settings.data_dir}/safesc.db', connect_args={'check_same_thread':False})
@event.listens_for(engine, 'connect')
def sqlite_options(connection, _):
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA busy_timeout=5000')

def init(recover=True):
    SQLModel.metadata.create_all(engine)
    if not recover:return
    with Session(engine) as s:
        for job in s.exec(select(Job).where(Job.status=='running')):
            job.status='failed'; job.error='Worker restarted; retry explicitly'; s.add(job)
        for src in s.exec(select(Source).where(Source.status=='streaming')):
            src.status='disconnected'; s.add(src)
        s.commit()

def save(obj):
    with Session(engine) as s:
        s.add(obj); s.commit(); s.refresh(obj); s.expunge(obj)
    return obj

def get(cls, key):
    with Session(engine) as s:
        return s.get(cls,key)

def rows(cls):
    with Session(engine) as s:
        return list(s.exec(select(cls)))
