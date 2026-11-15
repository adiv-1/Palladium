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
