"""Private, bounded Supabase archives. No Supabase credentials reach a browser.

SQLite stays the working index. Immutable, hashed originals and versioned JSON
manifests survive a pod replacement and can rebuild that index. Review history
is included in every changed snapshot; derived videos stay on the GPU disk.
"""
import asyncio,hashlib,json,mimetypes,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import quote,urlparse
import httpx
from .config import settings
from .db import ArchiveState,Incident,Source,ReviewAction,get,rows,save,now
from .video import digest
from .retention import held, expires_at, guarded

MAX_FILE_BYTES=49*1024**2
last_error=None
usage_bytes=0

def enabled():return bool(settings.supabase_url and settings.supabase_secret_key.get_secret_value())

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def snapshot(incident):
    source=get(Source,incident.source_id)
    return {'version':1,'incident':incident.model_dump(),
        'source':{k:v for k,v in source.model_dump().items() if k not in ['path','telemetry']} if source else None,
        'reviews':sorted([r.model_dump() for r in rows(ReviewAction) if r.incident_id==incident.id],key=lambda r:(r['timestamp'],r['id']))}

class SupabaseArchive:
    def __init__(self,client=None):
        url=settings.supabase_url.rstrip('/')
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.supabase.co') or parsed.path or parsed.username or parsed.password:
            raise ValueError('Expected the HTTPS Supabase project URL')
        self.bucket=settings.supabase_bucket
        self.client=client or httpx.Client(base_url=url,headers={'apikey':settings.supabase_secret_key.get_secret_value()},timeout=60,follow_redirects=False)
    def close(self):self.client.close()
    def request(self,method,path,**kwargs):
        response=self.client.request(method,'/storage/v1/'+path,**kwargs)
        if not response.is_success:raise RuntimeError(f'Supabase storage request failed (HTTP {response.status_code})')
        return response
    def ensure_private(self):
        response=self.client.get('/storage/v1/bucket/'+quote(self.bucket,safe=''))
        missing=response.status_code==404
        if response.status_code==400:
            try:missing=response.json().get('code')=='NoSuchBucket'
            except ValueError:pass
        if missing:
            self.request('POST','bucket',json={'id':self.bucket,'name':self.bucket,'public':False,
                'file_size_limit':MAX_FILE_BYTES,'allowed_mime_types':['video/mp4','image/jpeg','application/json']})
            response=self.client.get('/storage/v1/bucket/'+quote(self.bucket,safe=''))
        if not response.is_success:raise RuntimeError(f'Cannot inspect private archive bucket (HTTP {response.status_code})')
        if response.json().get('public') is not False:raise RuntimeError('Archive bucket must be private; refusing to upload')
    def objects(self,prefix=''):
        result=[];offset=0
        while True:
            batch=self.request('POST','object/list/'+quote(self.bucket,safe=''),json={'prefix':prefix,'limit':1000,'offset':offset,'sortBy':{'column':'name','order':'asc'}}).json()
            for item in batch:
                name=prefix+item['name']
                if item.get('id') is None:result.extend(self.objects(name+'/'))
                else:result.append({**item,'key':name})
            if len(batch)<1000:return result
            offset+=len(batch)
    def used_bytes(self):return sum(int(o.get('metadata',{}).get('size',0)) for o in self.objects())
    def upload(self,key,content,mime,upsert=False):
        if len(content)>MAX_FILE_BYTES:raise ValueError('Archive file exceeds the free-tier per-file limit; local original retained')
        path='object/'+quote(self.bucket,safe='')+'/'+quote(key,safe='/')
        response=self.client.post('/storage/v1/'+path,content=content,headers={'Content-Type':mime,'x-upsert':'true' if upsert else 'false'})
        if not response.is_success:
            # A same-name object is acceptable only when its exact bytes match.
            if response.status_code in [400,409] and not upsert:
                existing=self.download(key)
                if hashlib.sha256(existing).digest()==hashlib.sha256(content).digest():return
            raise RuntimeError(f'Supabase upload failed (HTTP {response.status_code}); local original retained')
    def download(self,key):return self.request('GET','object/authenticated/'+quote(self.bucket,safe='')+'/'+quote(key,safe='/')).content
    def delete(self,keys):
        if keys:self.request('DELETE','object/'+quote(self.bucket,safe=''),json={'prefixes':keys})

@guarded
def sync_incident(incident,store):
    global usage_bytes
    incident=get(Incident,incident.id) or incident
    snap=snapshot(incident);fingerprint=hashlib.sha256(canonical(snap)).hexdigest()
    state=get(ArchiveState,incident.id) or ArchiveState(incident_id=incident.id)
    if state.status in ['synced','evicted'] and state.fingerprint==fingerprint:return state
    folder=settings.data_dir/'incidents'/incident.id;media=dict(state.media);uploads=[]
    evicted=state.media_evicted
    if state.status=='expired' or held(incident) and evicted:
        media={};evicted=False
    for kind in ([] if evicted else ['original','thumbnail']+(['source_original'] if incident.record.get('files',{}).get('source_original') else [])):
        filename=incident.record.get('files',{}).get(kind)
        if not filename:raise ValueError('Original and thumbnail are required for a complete archive')
        path=(folder/filename).resolve()
        if path.parent!=folder.resolve() or not path.is_file():raise ValueError('Archive source is missing; local files retained')
        sha=digest(path)
        if kind=='original' and sha!=incident.record['original_sha256']:raise ValueError('Original hash mismatch; refusing archive')
        if kind=='source_original' and sha!=incident.record.get('source_sha256'):raise ValueError('Original upload hash mismatch; refusing archive')
        if media.get(kind,{}).get('sha256')==sha:continue
        size=path.stat().st_size
        if size>MAX_FILE_BYTES:raise ValueError('Original exceeds 49 MiB archive limit; retained locally without recompression')
        key=f'incidents/{incident.id}/{kind}-{sha}{path.suffix}'
        media[kind]={'key':key,'sha256':sha,'bytes':size,'filename':path.name}
        uploads.append((key,path,mimetypes.guess_type(path.name)[0] or 'application/octet-stream'))
    expires=expires_at(incident)
    manifest={**snap,'media':media,'expires_at':expires,'media_evicted':evicted,
        'archive_policy':'Original + untouched upload when retained + thumbnail + review metadata. Explicit retention holds do not expire or enter FIFO eviction until released. Other media expire normally and oldest unheld media are evicted at capacity. New backups fail rather than exceed the cap. Derived media remain on Runpod.'}
    body=canonical(manifest);key=f'manifests/{incident.id}/{fingerprint}.json'
    pointer=canonical({'manifest_key':key,'expires_at':expires,'incident_id':incident.id})
    planned=sum(path.stat().st_size for _,path,_ in uploads)+len(body)+len(pointer)
    make_room(store,planned,exclude=incident.id)
    for object_key,path,mime in uploads:
        content=path.read_bytes();store.upload(object_key,content,mime)
        if hashlib.sha256(store.download(object_key)).hexdigest()!=hashlib.sha256(content).hexdigest():
            raise ValueError('Remote original verification failed; local files retained')
    store.upload(key,body,'application/json')
    store.upload(f'index/{incident.id}.json',pointer,'application/json',upsert=True)
    state.status='evicted' if evicted else 'synced';state.media_evicted=evicted;state.fingerprint=fingerprint;state.manifest_key=key;state.media=media
    state.bytes_stored=sum(v['bytes'] for v in media.values())+len(body);state.updated_at=now();state.error=None
    usage_bytes=store.used_bytes()
    return save(state)

@guarded
def make_room(store,planned,exclude):
    """FIFO by incident capture time, confined to this managed bucket; keep an audit manifest."""
    global usage_bytes
    cap=settings.archive_max_mb*1024**2
    if planned>cap:raise ValueError('New incident cannot fit the archive cap; local media retained')
    usage_bytes=store.used_bytes()
    if usage_bytes+planned<=cap:return
    candidates=[]
    for item in store.objects('index/'):
        pointer=json.loads(store.download(item['key']))
        if pointer['incident_id']==exclude:continue
        manifest=json.loads(store.download(pointer['manifest_key']))
        local=get(Incident,pointer['incident_id'])
        if held(manifest['incident']) or local and held(local):continue
        if manifest.get('media'):candidates.append((manifest['incident']['created_at'],item['key'],manifest,pointer))
    for _,index_key,manifest,pointer in sorted(candidates,key=lambda entry:(entry[0],entry[1])):
        incident_id=manifest['incident']['id']
        # Save an explicit eviction revision before removing the media or old manifests.
        manifest={**manifest,'media':{},'media_evicted':True,'media_evicted_at':now(),'eviction_reason':'FIFO archive capacity'}
        body=canonical(manifest);key=f'manifests/{incident_id}/evicted-{hashlib.sha256(body).hexdigest()}.json'
        revised_pointer=canonical({**pointer,'manifest_key':key})
        # Leave even temporary audit writes inside the managed cap.
        overhead=len(body)+max(0,len(revised_pointer)-len(store.download(index_key)))
        if store.used_bytes()+overhead>cap:continue
        store.upload(key,body,'application/json')
        store.upload(index_key,revised_pointer,'application/json',upsert=True)
        keys=[x['key'] for x in store.objects(f'incidents/{incident_id}/')]
        keys += [x['key'] for x in store.objects(f'manifests/{incident_id}/') if x['key']!=key]
        store.delete(keys)
        state=get(ArchiveState,incident_id) or ArchiveState(incident_id=incident_id)
        state.status='evicted';state.media_evicted=True;state.media={};state.manifest_key=key;state.bytes_stored=len(body);state.updated_at=now();save(state)
        usage_bytes=store.used_bytes()
        if usage_bytes+planned<=cap:return
    raise ValueError('Archive cap is occupied by held media, metadata, or other objects; local original retained')

@guarded
def restore_archive(store):
    """Restore unexpired originals/index after replacing the GPU pod; preserve existing human decisions."""
    restored=[]
    for item in store.objects('index/'):
        pointer=json.loads(store.download(item['key']))
        manifest=json.loads(store.download(pointer['manifest_key']));record=manifest['incident']
        expiry=None if held(record) else manifest.get('expires_at') or pointer.get('expires_at') or expires_at(record)
        if expiry is not None and expiry<=now():continue
        if get(Incident,record['id']):continue
        incident=Incident.model_validate(record)
        # UUID validation prevents a remote manifest from choosing a local path.
        from uuid import UUID
        UUID(incident.id);folder=settings.data_dir/'incidents'/incident.id;folder.mkdir(exist_ok=True,parents=True)
        files={}
        for kind,meta in manifest['media'].items():
            if kind=='original' and meta['sha256']!=incident.record['original_sha256']:
                raise ValueError('Archived original hash does not match incident provenance')
            if kind=='source_original' and meta['sha256']!=incident.record.get('source_sha256'):
                raise ValueError('Archived original upload hash does not match source provenance')
            filename=Path(meta['filename']).name
            if filename!=meta['filename']:raise ValueError('Invalid archived filename')
            content=store.download(meta['key'])
            if hashlib.sha256(content).hexdigest()!=meta['sha256']:raise ValueError('Archived media hash mismatch')
            target=folder/filename
            if target.exists():
                if digest(target)!=meta['sha256']:raise ValueError('Restore refuses to overwrite different local media')
            else:
                with target.open('xb') as f:f.write(content)
                target.chmod(0o444)
            files[kind]=filename
        source=manifest.get('source')
        if source and not get(Source,source['id']):save(Source.model_validate(source))
        incident.record={**incident.record,'files':files,'restored_from_archive':True,
            'media_evicted':manifest.get('media_evicted',False)};save(incident)
        for review in manifest.get('reviews',[]):
            if not get(ReviewAction,review['id']):save(ReviewAction.model_validate(review))
        save(ArchiveState(incident_id=incident.id,status='evicted' if manifest.get('media_evicted') else 'synced',media_evicted=manifest.get('media_evicted',False),manifest_key=pointer['manifest_key'],media=manifest['media'],
            fingerprint=hashlib.sha256(canonical(snapshot(incident))).hexdigest()))
        restored.append(incident.id)
    return restored

@guarded
def expire_archive(incident,store):
    """Recheck the hold under the same lock used by hold updates and FIFO."""
    incident=get(Incident,incident.id) or incident
    expiry=expires_at(incident)
    if expiry is None or expiry>now():return False
    state=get(ArchiveState,incident.id)
    if state and state.status!='expired':
        keys=[x['key'] for prefix in [f'incidents/{incident.id}/',f'manifests/{incident.id}/'] for x in store.objects(prefix)]
        store.delete(keys+[f'index/{incident.id}.json'])
        state.status='expired';state.updated_at=now();save(state)
    return True


async def archive_worker():
    global last_error,usage_bytes
    if not enabled():return
    retry_after={}
    while True:
        store=None
        try:
            store=SupabaseArchive();await asyncio.to_thread(store.ensure_private);last_error=None
            # Purge the whole incident prefix, including historical manifests, after retention expires.
            for incident in rows(Incident):
                if await asyncio.to_thread(expire_archive,incident,store):continue
                if retry_after.get(incident.id,0)>time.monotonic():continue
                try:
                    await asyncio.to_thread(sync_incident,incident,store)
                except Exception as error:
                    state=get(ArchiveState,incident.id) or ArchiveState(incident_id=incident.id)
                    state.status='failed';state.error=str(error);state.updated_at=now();save(state)
                    retry_after[incident.id]=time.monotonic()+60
        except asyncio.CancelledError:raise
        except Exception as error:
            # Configuration/network errors surface on the authenticated storage status endpoint.
            last_error=str(error)
        finally:
            if store:store.close()
        await asyncio.sleep(5)
