from apps.api.db import Incident,Job
from apps.api.presentation import processing_summary


def test_queue_processing_preserves_success_after_historical_failure_and_shows_new_work():
    incident=Incident(source_id='source',start_time=0,end_time=1,severity='WATCH',confidence=0,
        record={'clip_review':{'report':{'takeaway':'Visible movement'}}})
    failed=Job(incident_id=incident.id,kind='review_brief',status='failed')
    complete=Job(incident_id=incident.id,kind='review_brief',status='completed')
    jobs=[failed,complete]
    assert processing_summary(incident,jobs)['review_brief']['status']=='completed'
    queued=Job(incident_id=incident.id,kind='review_brief',status='queued');jobs.append(queued)
    assert processing_summary(incident,jobs)['review_brief']['status']=='queued'
    queued.status='running'
    assert processing_summary(incident,jobs)['review_brief']['status']=='running'
    assert processing_summary(incident,jobs)['running']==1
    assert incident.record=={'clip_review':{'report':{'takeaway':'Visible movement'}}}


def test_queue_processing_does_not_invent_completed_analysis():
    incident=Incident(source_id='source',start_time=0,end_time=1,severity='WATCH',confidence=0)
    assert processing_summary(incident,[])['review_brief']['status']=='not_requested'
    failed=Job(incident_id=incident.id,kind='review_brief',status='failed')
    assert processing_summary(incident,[failed])['review_brief']['status']=='failed'


def test_worker_recovery_clears_live_location_without_rewriting_saved_incident():
    from apps.api.db import Source,init,save,get
    init(recover=False)
    fix={'status':'active','latitude':34.0,'longitude':-118.0,'accuracy_m':10,'measured_at':1234}
    source=save(Source(name='Interrupted location test',kind='phone',status='streaming',telemetry={'geolocation':fix}))
    incident=save(Incident(source_id=source.id,start_time=0,end_time=1,severity='WATCH',confidence=0,record={'geolocation':fix}))
    init()
    recovered=get(Source,source.id)
    assert recovered.status=='disconnected'
    assert recovered.telemetry['geolocation']['status']=='disabled'
    assert 'latitude' not in recovered.telemetry['geolocation']
    assert get(Incident,incident.id).record['geolocation']==fix
