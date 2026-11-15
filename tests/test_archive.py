import hashlib,json
from datetime import datetime,timedelta,timezone
import httpx,pytest
from apps.api.archive import SupabaseArchive,sync_incident,restore_archive
from apps.api.config import settings
from apps.api.db import init,save,get,Incident,Source,ReviewAction,ArchiveState

class MemoryStore:
    def __init__(self):self.data={};self.deletions=[]
    def upload(self,key,body,mime,upsert=False):
        assert upsert or key not in self.data or self.data[key]==body
        self.data[key]=body
    def download(self,key):return self.data[key]
    def objects(self,prefix=''):
        return [{'key':k,'metadata':{'size':len(v)}} for k,v in self.data.items() if k.startswith(prefix)]
    def used_bytes(self):return sum(map(len,self.data.values()))
    def delete(self,keys):
        for key in keys:self.deletions.append(key);self.data.pop(key,None)

def incident(size=20,age=0):
    init(recover=False);s=save(Source(name='Staged archive test',kind='upload'))
    i=Incident(source_id=s.id,start_time=0,end_time=1,severity='WATCH',confidence=0,
        created_at=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat())
    folder=settings.data_dir/'incidents'/i.id;folder.mkdir(parents=True)
    content=b'x'*size;(folder/'original.mp4').write_bytes(content);(folder/'thumbnail.jpg').write_bytes(b'jpeg')
    i.record={'original_sha256':hashlib.sha256(content).hexdigest(),'files':{'original':'original.mp4','thumbnail':'thumbnail.jpg'}}
    return save(i)

def test_private_bucket_is_required(monkeypatch):
    monkeypatch.setattr(settings,'supabase_url','https://example.supabase.co')
    client=httpx.Client(base_url=settings.supabase_url,transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'public':True})))
    store=SupabaseArchive(client)
    with pytest.raises(RuntimeError,match='must be private'):store.ensure_private()
    store.close()

def test_archive_hashes_original_keeps_review_versions_and_rejects_tampering():
    i=incident();store=MemoryStore();first=sync_incident(i,store)
    assert first.status=='synced'
    save(ReviewAction(incident_id=i.id,decision='dismissed',note='Staged test'))
    second=sync_incident(i,store)
    assert second.manifest_key!=first.manifest_key
    manifest=json.loads(store.download(second.manifest_key))
    assert manifest['reviews'][0]['decision']=='dismissed'
    assert first.manifest_key in store.data # prior review snapshot is immutable
    (settings.data_dir/'incidents'/i.id/'original.mp4').write_bytes(b'tampered')
    # Trigger another snapshot so content integrity is checked rather than returning a cached state.
    i.review_status='needs_review';save(i)
    with pytest.raises(ValueError,match='hash mismatch'):sync_incident(i,store)

def test_fifo_evicts_oldest_video_and_never_reuploads_it(monkeypatch):
    monkeypatch.setattr(settings,'archive_max_mb',1)
    old=incident(600_000,age=60);new=incident(600_000);store=MemoryStore()
    sync_incident(old,store);old_state=get(ArchiveState,old.id);old_media=old_state.media['original']['key']
    sync_incident(new,store)
    assert old_media not in store.data
    evicted=get(ArchiveState,old.id);assert evicted.status=='evicted' and evicted.media_evicted
    assert json.loads(store.download(evicted.manifest_key))['media_evicted'] is True
    assert store.used_bytes()<=1024**2
    count=len(store.deletions);sync_incident(old,store)
    assert len(store.deletions)==count and old_media not in store.data
    save(ReviewAction(incident_id=old.id,decision='dismissed',note='After eviction'))
    sync_incident(old,store)
    assert old_media not in store.data # metadata updates cannot resurrect old videos
    assert get(ArchiveState,new.id).status=='synced'

def test_restore_rebuilds_original_and_review_history_without_overwriting_decisions():
    from sqlmodel import Session
    from apps.api.db import engine
    i=incident();review=save(ReviewAction(incident_id=i.id,decision='dismissed',note='Restore test'))
    store=MemoryStore();state=sync_incident(i,store)
    with Session(engine) as session:
        for cls,key in [(ArchiveState,i.id),(ReviewAction,review.id),(Incident,i.id),(Source,i.source_id)]:
            session.delete(session.get(cls,key))
        session.commit()
    folder=settings.data_dir/'incidents'/i.id
    for name in ['original.mp4','thumbnail.jpg']:(folder/name).unlink()
    assert restore_archive(store)==[i.id]
    assert hashlib.sha256((folder/'original.mp4').read_bytes()).hexdigest()==i.record['original_sha256']
    assert get(ReviewAction,review.id).note=='Restore test'
    changed=get(Incident,i.id);changed.review_status='needs_review';save(changed)
    assert restore_archive(store)==[] and get(Incident,i.id).review_status=='needs_review'


def test_retention_hold_is_audited_idempotent_and_blocks_local_and_archive_expiry():
    from apps.api.retention import set_hold,held
    from apps.api.archive import expire_archive
    from apps.api.jobs import expire_local
    from apps.api.db import rows
    i=incident(age=(settings.retention_hours+1)*3600);store=MemoryStore()
    held_incident=set_hold(i.id,True,'Keep this original for recall')
    again=set_hold(i.id,True,'Repeated save should not duplicate the audit')
    assert held(again) and again.review_status==i.review_status
    assert len([r for r in rows(ReviewAction) if r.incident_id==i.id])==1
    state=sync_incident(held_incident,store)
    manifest=json.loads(store.download(state.manifest_key))
    assert manifest['expires_at'] is None
    assert json.loads(store.download(f'index/{i.id}.json'))['expires_at'] is None
    assert expire_archive(i,store) is False # stale caller cannot bypass the fresh hold
    expire_local()
    assert (settings.data_dir/'incidents'/i.id/'original.mp4').is_file()
    set_hold(i.id,False,'Return to the normal retention policy')
    set_hold(i.id,False,'Repeated release is also idempotent')
    assert len([r for r in rows(ReviewAction) if r.incident_id==i.id])==2
    assert expire_archive(i,store) is True
    expire_local()
    assert not (settings.data_dir/'incidents'/i.id).exists()
    assert get(Incident,i.id).record['expired'] is True


def test_fifo_checks_fresh_hold_before_manifest_sync_and_never_exceeds_cap(monkeypatch):
    from apps.api.retention import set_hold
    monkeypatch.setattr(settings,'archive_max_mb',1)
    old=incident(600_000,age=60);new=incident(600_000);store=MemoryStore()
    state=sync_incident(old,store);key=state.media['original']['key']
    set_hold(old.id,True,'Preserve this report for later recall') # cloud manifest is still unheld
    with pytest.raises(ValueError,match='cap'):sync_incident(new,store)
    assert key in store.data and store.used_bytes()<=1024**2
    held_state=sync_incident(old,store)
    assert json.loads(store.download(held_state.manifest_key))['expires_at'] is None
    set_hold(old.id,False,'Release the explicitly saved report')
    sync_incident(old,store)
    sync_incident(new,store)
    assert key not in store.data and store.used_bytes()<=1024**2


def test_hold_keeps_exact_uploaded_original_and_restores_past_normal_expiry():
    from sqlmodel import Session
    from apps.api.db import engine,rows
    from apps.api.retention import set_hold,held
    i=incident(age=(settings.retention_hours+1)*3600)
    source=get(Source,i.source_id);uploads=settings.data_dir/'uploads';uploads.mkdir(exist_ok=True)
    path=uploads/(source.id+'.mp4');content=b'untouched uploaded MP4 bytes'
    path.write_bytes(content);source.path=str(path);source.original_sha256=hashlib.sha256(content).hexdigest();save(source)
    kept=set_hold(i.id,True,'Save the exact uploaded original for recall')
    retained=settings.data_dir/'incidents'/i.id/kept.record['files']['source_original']
    assert retained.read_bytes()==content and kept.record['source_sha256']==source.original_sha256
    store=MemoryStore();state=sync_incident(kept,store)
    assert store.download(state.media['source_original']['key'])==content
    # A second identical hold does not add another raw copy, audit event, or manifest.
    assert set_hold(i.id,True,'Repeated owner save').record==kept.record
    assert sync_incident(i,store).manifest_key==state.manifest_key
    with Session(engine) as session:
        for row in [*filter(lambda r:r.incident_id==i.id,rows(ReviewAction)),get(ArchiveState,i.id),get(Incident,i.id)]:
            session.delete(session.get(type(row),row.id if isinstance(row,ReviewAction) else row.incident_id if isinstance(row,ArchiveState) else row.id))
        session.commit()
    import shutil
    shutil.rmtree(retained.parent)
    assert restore_archive(store)==[i.id]
    restored=get(Incident,i.id)
    assert held(restored) and (retained.parent/restored.record['files']['source_original']).read_bytes()==content
    assert get(Source,i.source_id).original_sha256==source.original_sha256


def test_hold_rearchives_evicted_local_video_and_rejects_missing_or_changed_original(monkeypatch):
    from apps.api.retention import set_hold
    monkeypatch.setattr(settings,'archive_max_mb',1)
    old=incident(600_000,age=60);new=incident(600_000);store=MemoryStore()
    sync_incident(old,store);sync_incident(new,store)
    assert get(ArchiveState,old.id).media_evicted
    kept=set_hold(old.id,True,'Keep the still available local video')
    state=sync_incident(kept,store)
    assert state.status=='synced' and not state.media_evicted and 'original' in state.media
    missing=incident();(settings.data_dir/'incidents'/missing.id/'original.mp4').unlink()
    with pytest.raises(ValueError,match='unavailable'):set_hold(missing.id,True,'Cannot recreate missing original video')
    changed=incident();(settings.data_dir/'incidents'/changed.id/'original.mp4').write_bytes(b'different')
    with pytest.raises(ValueError,match='hash mismatch'):set_hold(changed.id,True,'Cannot preserve changed evidence')


def test_retention_hold_endpoint_requires_auth_and_appends_independent_audit():
    from fastapi.testclient import TestClient
    from apps.api.main import app
    client=TestClient(app);i=incident()
    route=f'/api/incidents/{i.id}/retention-hold'
    payload={'active':True,'note':'Owner requested later recall'}
    assert client.post(route,json=payload).status_code==401
    headers={'Authorization':'Bearer '+settings.app_token}
    response=client.post(route,json=payload,headers=headers)
    assert response.status_code==200 and response.json()['record']['retention_hold']['active'] is True
    assert response.json()['review_status']==i.review_status
    assert client.post('/api/incidents/missing/retention-hold',json=payload,headers=headers).status_code==404
    assert client.post(route,json={'active':False,'note':'x'},headers=headers).status_code==422
