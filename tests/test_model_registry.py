import hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from apps.api.model_registry import stage_adapter,publish_adapter

def adapter(tmp_path):
    root=tmp_path/'adapter';root.mkdir()
    for name in ('adapter_model.safetensors','adapter_config.json'):(root/name).write_bytes(name.encode())
    sha=lambda name:hashlib.sha256((root/name).read_bytes()).hexdigest()
    report={'base_model':'Qwen/Qwen3-VL-4B-Instruct','base_revision':'pinned',
        'adapter_sha256':sha('adapter_model.safetensors'),'adapter_config_sha256':sha('adapter_config.json'),
        'promotion_passed':False,'dataset':{'private_path':'/private/source.mp4'},
        'candidate_predictions':[{'private_annotation':'Do not upload'}],'baseline':{'test':{'action_accuracy':.5}}}
    (root/'evaluation.json').write_text(json.dumps(report));(root/'private-video.mp4').write_bytes(b'private')
    return root

def test_registry_allowlist_excludes_footage_annotations_and_raw_evaluation(tmp_path):
    source=adapter(tmp_path);out=tmp_path/'staged';manifest=stage_adapter(source,'candidate-v1','candidate',out)
    assert {p.name for p in out.iterdir()}=={'adapter_model.safetensors','adapter_config.json','evaluation.json','manifest.json','README.md'}
    clean=json.loads((out/'evaluation.json').read_text())
    assert 'dataset' not in clean and 'candidate_predictions' not in clean
    assert clean['promotion_passed'] is False and manifest['publication_changes_live_model'] is False
    assert (out/'adapter_model.safetensors').read_bytes()==(source/'adapter_model.safetensors').read_bytes()
    (source/'adapter_model.safetensors').write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash'):stage_adapter(source,'candidate-v2','candidate',tmp_path/'bad')

def test_registry_requires_private_repository_and_verifies_published_version(tmp_path):
    source=adapter(tmp_path)
    class API:
        private=False
        def __init__(self):self.files=[];self.uploaded=False
        def create_repo(self,**kwargs):assert kwargs['private'] is True
        def model_info(self,repo):return SimpleNamespace(private=self.private,sha='commit')
        def list_repo_files(self,**kwargs):return self.files
        def upload_folder(self,**kwargs):
            self.uploaded=True;self.files=[kwargs['path_in_repo']+'/'+p.name for p in Path(kwargs['folder_path']).iterdir()]
            return SimpleNamespace(oid='commit')
        def create_tag(self,**kwargs):assert kwargs['revision']=='commit' and kwargs['tag']=='candidate-v1'
    api=API()
    with pytest.raises(ValueError,match='private'):publish_adapter(source,'candidate-v1',repo_id='owner/private',api=api)
    assert not api.uploaded
    api.private=True
    result=publish_adapter(source,'candidate-v1',repo_id='owner/private',api=api)
    assert result['status']=='published' and result['tag']=='candidate-v1' and result['deployed'] is False

def test_training_progress_is_real_atomic_metadata_not_simulated_timers(tmp_path):
    from scripts.train_learning import progress
    progress(tmp_path,'training',step=3,total_steps=20,loss=1.25)
    value=json.loads((tmp_path/'progress.json').read_text())
    assert value['step']==3 and value['loss']==1.25 and value['stage']=='training'
    assert not (tmp_path/'.progress.json.tmp').exists()

def test_training_schedule_consumes_new_recordings_and_never_held_out_targets():
    import random
    from scripts.train_learning import training_schedule
    records=[{'id':f'{i}-{variant}','group_id':str(i),'split':'train'} for i in range(20) for variant in range(4)]
    records.append({'id':'held-out','group_id':'test-group','split':'test'})
    schedule=training_schedule(records,20,random.Random(42))
    assert len(schedule)==40 and len({r['group_id'] for r in schedule[:20]})==20
    assert all(r['split']=='train' for r in schedule)
    with pytest.raises(ValueError,match='cover every'):training_schedule(records,2,random.Random(42))
