import numpy as np
import pytest
from apps.api.config import settings
from apps.api.db import Source,ReviewAction,rows,save,init
from apps.api.video import Frame,encode,write_clip,digest
from scripts.request_upload_review import request_review

def test_requested_review_preserves_original_and_is_not_an_automatic_alert(tmp_path,monkeypatch):
    init(recover=False)
    monkeypatch.setattr(settings,'data_dir',tmp_path)
    for flag in ['enable_clip_review','enable_relay_vsr','enable_relift']:monkeypatch.setattr(settings,flag,False)
    video=tmp_path/'input.mp4';jpeg=encode(np.full((64,96,3),100,np.uint8))
    write_clip([Frame(i/10,i,jpeg) for i in range(10)],video)
    source=save(Source(name='Requested test upload',kind='upload',status='completed',path=str(video),original_sha256=digest(video)))
    incident=request_review(source.id)
    assert incident.confidence==0 and incident.record['trigger_origin']=='reviewer'
    assert incident.record['request_kind']=='complete-upload-review'
    assert digest(tmp_path/'incidents'/incident.id/'original.mp4')==digest(video)
    assert 'not an automatic' in next(r.note for r in rows(ReviewAction) if r.incident_id==incident.id)
    assert request_review(source.id).id==incident.id
    bad=save(Source(name='Active upload',kind='upload',status='streaming',path=str(video)))
    with pytest.raises(ValueError):request_review(bad.id)
