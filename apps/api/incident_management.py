"""Audited reviewer labels and reversible queue removal; never erase evidence."""
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from .db import Incident, Job, ReviewAction, engine, now
from .retention import guarded

ACTIVE_JOBS = {'queued', 'running', 'cancelling'}


class QueueChange(BaseModel):
    note: str = Field(default='', max_length=2000)


class ReviewedLabel(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    action: Literal['ordinary_activity','physical_contact','object_taking','insufficient_visibility']
    tags: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default='', max_length=2000)

    @field_validator('title','description')
    @classmethod
    def visible_text(cls,value):
        value=value.strip()
        if not value:raise ValueError('Reviewer text must not be empty')
        return value

    @field_validator('tags')
    @classmethod
    def clean_tags(cls,values):
        result=[]
        for value in values:
            value=value.strip()
            if not value or len(value)>40:raise ValueError('Tags must contain 1 to 40 characters')
            if value not in result:result.append(value)
        return result


def deleted(incident):
    record=incident.get('record',{}) if isinstance(incident,dict) else incident.record
    return record.get('queue_deleted',{}).get('active') is True


def active_processing(session,incident_id):
    return any(job.status in ACTIVE_JOBS for job in session.exec(select(Job).where(Job.incident_id==incident_id)))


@guarded
def set_queue_deleted(incident_id,active,note=''):
    with Session(engine) as session:
        incident=session.get(Incident,incident_id)
        if incident is None:raise LookupError('Incident not found')
        if deleted(incident)==active:
            session.expunge(incident)
            return incident
        if active and active_processing(session,incident_id):
            raise ValueError('Wait for this incident processing to finish before moving it to Trash')
        stamp=now()
        state={**incident.record.get('queue_deleted',{}),'active':active,'updated_at':stamp,
            'note':note,'provenance':'workspace_reviewer',
            'deleted_at' if active else 'restored_at':stamp}
        incident.record={**incident.record,'queue_deleted':state}
        session.add(incident)
        session.add(ReviewAction(incident_id=incident_id,
            decision='queue_deleted' if active else 'queue_restored',
            note=note or ('Moved to Trash; media and provenance retained.' if active else 'Restored to the incident queue.')))
        session.commit();session.refresh(incident);session.expunge(incident)
        return incident


@guarded
def set_reviewed_label(incident_id,payload):
    with Session(engine) as session:
        incident=session.get(Incident,incident_id)
        if incident is None:raise LookupError('Incident not found')
        if active_processing(session,incident_id):
            raise ValueError('Wait for this incident processing to finish before editing reviewer labels')
        values=payload.model_dump()
        previous=incident.record.get('reviewed_label',{})
        # Retried submissions do not fabricate duplicate human review history.
        if all(previous.get(k)==v for k,v in values.items()):
            session.expunge(incident)
            return incident
        label={**values,'updated_at':now(),'provenance':'workspace_reviewer'}
        incident.record={**incident.record,'reviewed_label':label}
        session.add(incident)
        session.add(ReviewAction(incident_id=incident_id,decision='label_updated',
            note=json.dumps({'label':values,'previous':{k:previous[k] for k in values if k in previous},
                'notice':'Reviewer presentation label only; original model output and training permissions are unchanged.'},sort_keys=True)))
        session.commit();session.refresh(incident);session.expunge(incident)
        return incident
