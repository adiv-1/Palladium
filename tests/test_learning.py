import json
from pathlib import Path
from datetime import datetime,timezone,timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from apps.api import db
from apps.api.main import app
from apps.api.config import settings
from apps.api.video import Frame,encode,write_clip,digest
from apps.api.learning import (LearningAsset,LearningPermission,LearningAnnotation,DatasetSnapshot,
    AnnotationInput,register_local,annotate,asset_record,build_snapshot,expire_learning,retain_incident)

@pytest.fixture
def learning_env(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    monkeypatch.setattr(db,'engine',create_engine(f'sqlite:///{tmp_path}/test.sqlite',connect_args={'check_same_thread':False}))
    db.init()
    path=tmp_path/'source.mp4'
    write_clip([Frame(n/4,n,encode(np.full((64,96,3),n*10,np.uint8))) for n in range(12)],path)
    return path

def approved(asset):
    db.save(LearningPermission(subject='asset:'+asset.id,permission='permitted',note='Consenting staged demonstration footage'))
    return annotate(asset,AnnotationInput(action='physical_contact',start=.3,end=1.8,description='One person pushes another person.'))

def test_permission_and_append_only_labels_gate_dataset(learning_env):
    original_hash=digest(learning_env)
    asset=register_local(learning_env,'Staged recording','parent-1','upload')
    assert not asset_record(asset)['eligible']
    with pytest.raises(ValueError):build_snapshot()
    first=approved(asset);assert asset_record(asset)['eligible']
    snapshot=build_snapshot();manifest=json.loads(Path(snapshot.path).read_text())
    assert manifest['examples'][0]['annotation']['id']==first.id
    annotate(asset,AnnotationInput(action='physical_contact',start=.3,end=1.8,description='One person pushes another person.',status='rejected',note='Corrected after review'))
    assert not asset_record(asset)['eligible']
    assert len(db.rows(LearningAnnotation))==2
    assert json.loads(Path(snapshot.path).read_text())==manifest
    assert digest(learning_env)==original_hash

def test_external_context_and_ambiguous_labels_rejected():
    with pytest.raises(ValueError):AnnotationInput(action='physical_contact',start=1,end=2,basis='external_context',description='A person pushes another person.')
    with pytest.raises(ValueError):AnnotationInput(action='insufficient_visibility',start=1,end=2)
    with pytest.raises(ValueError):AnnotationInput(action='object_taking',start=1,end=2,description='A criminal commits theft.')
    with pytest.raises(ValueError):AnnotationInput(action='physical_contact',start=float('nan'),end=2)

def test_source_permission_revocation_removes_eligibility(learning_env):
    source=db.save(db.Source(name='Staged camera',kind='phone'))
    incident=db.save(db.Incident(source_id=source.id,start_time=0,end_time=2,severity='WATCH',confidence=.8,review_status='confirmed',record={}))
    folder=settings.data_dir/'incidents'/incident.id;folder.mkdir(parents=True)
    (folder/'original.mp4').write_bytes(learning_env.read_bytes())
    assert retain_incident(incident) is None
    db.save(LearningPermission(subject='source:'+source.id,permission='permitted',note='Permission from the staged footage owner'))
    asset=retain_incident(incident);approved(asset);assert asset_record(asset)['eligible']
    db.save(LearningPermission(subject='source:'+source.id,permission='denied',note='Owner revoked future training reuse'))
    assert not asset_record(asset)['eligible']

def test_learning_upload_auth_dedup_and_annotations(learning_env):
    c=TestClient(app);headers={'Authorization':'Bearer test-workspace-token'}
    assert c.get('/api/learning/summary').status_code==401
    with learning_env.open('rb') as f:r=c.post('/api/learning/upload',headers=headers,files={'file':('demo.mp4',f,'video/mp4')})
    assert r.status_code==200,r.text
    asset=r.json();assert not asset['eligible'] and asset['permission']=='unknown'
    with learning_env.open('rb') as f:r=c.post('/api/learning/upload',headers=headers,files={'file':('again.mp4',f,'video/mp4')})
    assert r.json()['id']==asset['id']
    r=c.post(f"/api/learning/assets/{asset['id']}/annotations",headers=headers,json={'action':'ordinary_activity','start':0,'end':99})
    assert r.status_code==422
    r=c.post(f"/api/learning/assets/{asset['id']}/annotations",headers=headers,json={'action':'ordinary_activity','start':0,'end':2})
    assert r.status_code==200
    assert c.post('/api/learning/snapshots',headers=headers).status_code==409
    assert c.post(f"/api/learning/assets/{asset['id']}/permission",headers=headers,json={'permission':'permitted','note':'Owned staged video consent'}).status_code==200
    assert c.post('/api/learning/snapshots',headers=headers).status_code==200

def test_expiry_removes_only_retained_copy(learning_env):
    asset=register_local(learning_env,'Expired original','parent','upload');approved(asset)
    asset.expires_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat();db.save(asset)
    expire_learning()
    assert not Path(asset.path).exists() and learning_env.exists()
    assert not asset_record(asset)['eligible']

def test_related_recordings_cannot_cross_splits(learning_env):
    first=register_local(learning_env,'First view','same-original','research',provenance={'official_split':'train'});approved(first)
    other=settings.data_dir/'other.mp4'
    write_clip([Frame(n/4,n,encode(np.full((64,96,3),n*7,np.uint8))) for n in range(12)],other)
    second=register_local(other,'Derived view','same-original','research',provenance={'official_split':'test'});approved(second)
    with pytest.raises(ValueError,match='multiple dataset splits'):build_snapshot()

def test_revoked_asset_excluded_from_new_snapshots(learning_env):
    asset=register_local(learning_env,'Owned example','parent','upload');approved(asset)
    before=build_snapshot();assert before.record['total']==1
    from apps.api.learning import validate_snapshot_current
    manifest=json.loads(Path(before.path).read_text());validate_snapshot_current(manifest)
    db.save(LearningPermission(subject='asset:'+asset.id,permission='denied',note='Permission withdrawn before another training run'))
    with pytest.raises(ValueError,match='no-longer-permitted'):validate_snapshot_current(manifest)
    with pytest.raises(ValueError):build_snapshot()
    assert len(db.rows(DatasetSnapshot))==1

def test_model_action_consistency():
    from apps.api.schemas import Detection
    with pytest.raises(ValueError):Detection(state='NORMAL',action='physical_contact',confidence=.9,observable_events=[],uncertainties=[],supporting_frames=[])

def test_metadata_import_does_not_interrupt_live_sources(learning_env,monkeypatch,tmp_path):
    from scripts.sync_learning import main
    source=db.save(db.Source(name='Still live',kind='phone',status='streaming'))
    job=db.save(db.Job(incident_id='existing',kind='relift',status='running'))
    path=tmp_path/'export.json'
    monkeypatch.setattr('sys.argv',['sync_learning','export',str(path)]);main()
    monkeypatch.setattr('sys.argv',['sync_learning','import',str(path)]);main()
    assert db.get(db.Source,source.id).status=='streaming'
    assert db.get(db.Job,job.id).status=='running'

def test_exact_training_frame_routes_are_authenticated(learning_env):
    import hashlib
    from apps.api.learning import LearningRun
    root=settings.data_dir/'learning'/'datasets'/'frozen';root.mkdir(parents=True)
    frame=root/'frame.jpg';frame.write_bytes(b'reviewed-frame-bytes')
    record={'id':'asset-1','frames':['frame.jpg'],'frame_sha256':[digest(frame)],'split':'train','timestamps':[1.0]}
    records=root/'records.jsonl';records.write_text(json.dumps(record)+'\n')
    run=db.save(LearningRun(snapshot_id='snapshot',record={'data_relative_path':'datasets/frozen','records_sha256':digest(records)}))
    client=TestClient(app);path=f'/api/learning/runs/{run.id}/examples/asset-1/frames/0'
    assert client.get(path).status_code==401
    headers={'Authorization':'Bearer test-workspace-token'}
    assert client.get(path,headers=headers).content==b'reviewed-frame-bytes'
    assert client.get(path[:-1]+'9',headers=headers).status_code==404
    frame.write_bytes(b'changed')
    assert client.get(path,headers=headers).status_code==409

def test_failed_gate_requires_hash_bound_research_approval(tmp_path):
    import hashlib
    from ml.verifier import validate_adapter
    for name in ['adapter_model.safetensors','adapter_config.json']:(tmp_path/name).write_bytes(name.encode())
    report={'base_model':'Qwen/Qwen3-VL-4B-Instruct','base_revision':'revision','promotion_passed':False,
        'adapter_sha256':digest(tmp_path/'adapter_model.safetensors'),'adapter_config_sha256':digest(tmp_path/'adapter_config.json')}
    (tmp_path/'evaluation.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError,match='automatic promotion'):validate_adapter(tmp_path,'revision')
    (tmp_path/'research-approval.json').write_text(json.dumps({'owner_approved':True,'adapter_sha256':report['adapter_sha256']}))
    assert validate_adapter(tmp_path,'revision',True)['promotion_passed'] is False
    with pytest.raises(RuntimeError,match='revision'):validate_adapter(tmp_path,'other-base',True)
    (tmp_path/'adapter_model.safetensors').write_bytes(b'modified')
    with pytest.raises(RuntimeError,match='hash'):validate_adapter(tmp_path,'revision',True)
