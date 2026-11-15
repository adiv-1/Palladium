import copy
import json

import pytest
from fastapi.testclient import TestClient

from apps.api.config import settings
from apps.api.db import Incident, Source, ReviewAction, Job, init, save, get, rows
from apps.api.incident_management import ReviewedLabel, ReviewLocation, deleted, set_queue_deleted, set_reviewed_label, set_review_location
from apps.api.main import app
from apps.api.video import digest


@pytest.fixture(autouse=True)
def isolate_queue_test_records():
    from sqlmodel import Session
    from apps.api.db import engine
    init(recover=False)
    classes=(ReviewAction,Job,Incident,Source)
    before={cls:{item.id for item in rows(cls)} for cls in classes}
    yield
    with Session(engine) as session:
        for cls in classes:
            for item in rows(cls):
                if item.id not in before[cls]:
                    current=session.get(cls,item.id)
                    if current:session.delete(current)
        session.commit()


def sample(**changes):
    init(recover=False)
    source=save(Source(name='Queue test camera',kind='upload'))
    item=save(Incident(source_id=source.id,start_time=0,end_time=1,severity='REVIEW_REQUIRED',confidence=.81,
        record={'action':'physical_contact','observable_events':[{'observation':'Original model observation'}],
            'model_output_audit':[{'raw_json':'unchanged model output'}],'model':{'name':'fixture'},
            'original_sha256':'immutable-original-hash','files':{'original':'original.mp4'},**changes}))
    return item


def actions(item):
    return [action for action in rows(ReviewAction) if action.incident_id==item.id]


def test_reviewer_location_is_audited_idempotent_and_kept_separate_from_gps_and_review():
    gps={'status':'active','latitude':34.02,'longitude':-118.28,'accuracy_m':7,'provenance':'browser_permission'}
    item=sample(geolocation=gps,reviewed_label={'title':'Existing human label'})
    before=copy.deepcopy(item.record)
    payload=ReviewLocation(latitude=34.02145,longitude=-118.2904,label='Approximate recording area',note='Owner supplied this area')
    located=set_review_location(item.id,payload)
    assert located.review_status==item.review_status=='pending'
    assert located.record['reviewer_location']['precision']=='approximate'
    assert located.record['reviewer_location']['provenance']=='reviewer_supplied'
    assert 'accuracy_m' not in located.record['reviewer_location']
    assert {k:v for k,v in located.record.items() if k!='reviewer_location'}==before
    assert actions(item)[0].decision=='location_updated'
    set_review_location(item.id,payload);assert len(actions(item))==1
    cleared=set_review_location(item.id,ReviewLocation(active=False,note='Clear assigned area'))
    assert not cleared.record['reviewer_location']['active']
    assert cleared.record['geolocation']==gps
    assert len(actions(item))==2


def test_location_endpoint_requires_auth_and_rejects_fake_gps_or_review_fields():
    item=sample()
    with TestClient(app) as client:
        body={'latitude':34.02,'longitude':-118.29,'label':'An approximate area'}
        assert client.post(f'/api/incidents/{item.id}/location',json=body).status_code==401
        headers={'Authorization':'Bearer '+settings.app_token}
        for changes in ({'latitude':91},{'longitude':181},{'latitude':'NaN'},{'provenance':'browser_permission'},{'review_status':'confirmed'},{'label':''}):
            assert client.post(f'/api/incidents/{item.id}/location',json={**body,**changes},headers=headers).status_code==422
        response=client.post(f'/api/incidents/{item.id}/location',json=body,headers=headers)
        assert response.status_code==200 and response.json()['review_status']=='pending'
        assert response.json()['record']['reviewer_location']['provenance']=='reviewer_supplied'


def test_location_change_waits_for_processing_instead_of_overwriting_job_output():
    item=sample();save(Job(incident_id=item.id,kind='review_brief',status='running'))
    with pytest.raises(ValueError,match='processing'):
        set_review_location(item.id,ReviewLocation(latitude=34,longitude=-118,label='Area'))
    assert 'reviewer_location' not in get(Incident,item.id).record


def test_delete_restore_is_idempotent_audited_and_never_removes_held_media(tmp_path):
    item=sample(retention_hold={'active':True})
    folder=settings.data_dir/'incidents'/item.id;folder.mkdir(parents=True)
    media=folder/'original.mp4';media.write_bytes(b'Original immutable evidence')
    before=digest(media)
    tombstone=set_queue_deleted(item.id,True,'Hide duplicate from the queue')
    assert deleted(tombstone) and tombstone.record['retention_hold']['active']
    assert tombstone.record['original_sha256']=='immutable-original-hash'
    assert media.exists() and digest(media)==before
    assert actions(item)[0].decision=='queue_deleted'
    set_queue_deleted(item.id,True,'Retried request')
    assert len(actions(item))==1
    restored=set_queue_deleted(item.id,False,'Restore original queue item')
    assert not deleted(restored) and media.exists() and digest(media)==before
    assert restored.record['queue_deleted']['deleted_at']
    assert restored.record['queue_deleted']['restored_at']
    set_queue_deleted(item.id,False)
    assert [a.decision for a in actions(item)]==['queue_deleted','queue_restored']


@pytest.mark.parametrize('status',['queued','running','cancelling'])
def test_processing_incidents_cannot_be_deleted(status):
    item=sample();job=save(Job(incident_id=item.id,kind='relayvsr',status=status))
    with pytest.raises(ValueError,match='processing'):
        set_queue_deleted(item.id,True)
    with pytest.raises(ValueError,match='processing'):
        set_reviewed_label(item.id,ReviewedLabel(title='Wait',description='Processing',action='ordinary_activity'))
    assert not deleted(get(Incident,item.id)) and not actions(item)
    job.status='completed';save(job)
    assert deleted(set_queue_deleted(item.id,True))


def test_reviewer_labels_preserve_ai_evidence_review_status_and_training_permissions():
    item=sample(training_permission={'permission':'unknown'},retention_hold={'active':True})
    before=copy.deepcopy(item.record)
    label=ReviewedLabel(title=' Reviewed event ',description='Visible contact between two people.',
        action='physical_contact',tags=[' reviewed ','contact','contact'],note='Human viewing of original')
    updated=set_reviewed_label(item.id,label)
    assert updated.record['reviewed_label']['title']=='Reviewed event'
    assert updated.record['reviewed_label']['tags']==['reviewed','contact']
    assert updated.record['reviewed_label']['provenance']=='workspace_reviewer'
    assert updated.review_status==item.review_status
    assert {k:v for k,v in updated.record.items() if k!='reviewed_label'}==before
    assert actions(item)[0].decision=='label_updated'
    audit=json.loads(actions(item)[0].note)
    assert audit['label']['action']=='physical_contact'
    set_reviewed_label(item.id,label)
    assert len(actions(item))==1
    corrected=set_reviewed_label(item.id,ReviewedLabel(title='Ordinary activity',description='A normal exchange.',
        action='ordinary_activity',tags=['reviewed'],note='Correction after rewatching'))
    assert corrected.record['action']=='physical_contact'
    assert corrected.record['reviewed_label']['action']=='ordinary_activity'
    assert json.loads(actions(item)[-1].note)['previous']['title']=='Reviewed event'


def test_delete_restore_label_api_auth_and_deleted_only_list():
    visible=sample();trash=sample();set_queue_deleted(trash.id,True)
    # No lifespan/worker is needed for these synchronous database mutation checks.
    client=TestClient(app)
    assert client.delete('/api/incidents/'+visible.id).status_code==401
    assert client.post('/api/incidents/'+visible.id+'/restore').status_code==401
    assert client.post('/api/incidents/'+visible.id+'/label',json={}).status_code==401
    client.cookies.set('safesc_token',settings.app_token)
    ids={i['id'] for i in client.get('/api/incidents').json()}
    assert visible.id in ids and trash.id not in ids
    deleted_ids={i['id'] for i in client.get('/api/incidents?deleted=true').json()}
    assert trash.id in deleted_ids and visible.id not in deleted_ids
    response=client.request('DELETE','/api/incidents/'+visible.id,json={'note':'Test queue cleanup'})
    assert response.status_code==200 and response.json()['record']['queue_deleted']['active']
    assert client.post('/api/incidents/'+visible.id+'/restore').json()['record']['queue_deleted']['active'] is False
    response=client.post('/api/incidents/'+visible.id+'/label',json={'title':'Quiet footage',
        'description':'Ordinary movement is visible.','action':'ordinary_activity','tags':['quiet'],'note':'Reviewed original'})
    assert response.status_code==200 and response.json()['record']['reviewed_label']['title']=='Quiet footage'
    assert client.delete('/api/incidents/missing').status_code==404
    assert client.post('/api/incidents/missing/restore').status_code==404
    assert client.post('/api/incidents/'+visible.id+'/label',json={'title':'x','description':'y','action':'unknown'}).status_code==422


def test_reviewer_labels_allow_only_eight_short_literal_tags():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ReviewedLabel(title='Label',description='Description',action='ordinary_activity',tags=[str(i) for i in range(9)])
    with pytest.raises(ValidationError):
        ReviewedLabel(title='Label',description='Description',action='ordinary_activity',tags=['x'*41])
