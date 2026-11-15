"""Queue a clearly labeled, user-requested full review when an upload has no alert."""
import argparse
import json
import shutil
from pathlib import Path

from apps.api.config import settings
from apps.api.db import init,Source,Incident,Job,ReviewAction,get,rows,save
from apps.api.video import read_video,digest


def request_review(source_id):
    source=get(Source,source_id)
    if not source or source.kind!='upload' or source.status!='completed' or not source.path:
        raise ValueError('Use a completed uploaded video with available original media')
    if any(i.source_id==source_id and i.record.get('trigger_origin','model')!='reviewer' for i in rows(Incident)):
        raise ValueError('This upload already has an automatic incident; review that incident instead')
    existing=next((i for i in rows(Incident) if i.source_id==source_id and i.record.get('request_kind')=='complete-upload-review'),None)
    if existing:return existing
    path=Path(source.path)
    if digest(path)!=source.original_sha256:raise ValueError('Uploaded original hash changed')
    timestamps=[];thumbnail=None
    for frame in read_video(path):
        if frame.timestamp>60:raise ValueError('Full-clip review is limited to 60 seconds; select an incident window for longer uploads')
        timestamps.append({'index':frame.index,'timestamp':frame.timestamp})
        if thumbnail is None:thumbnail=frame.jpeg
    if len(timestamps)<2:raise ValueError('At least two video frames are required')
    incident=Incident(source_id=source.id,start_time=timestamps[0]['timestamp'],end_time=timestamps[-1]['timestamp'],severity='WATCH',confidence=0)
    folder=settings.data_dir/'incidents'/incident.id;folder.mkdir(parents=True)
    original=folder/'original.mp4'
    with path.open('rb') as src,original.open('xb') as dest:shutil.copyfileobj(src,dest)
    original.chmod(0o444);(folder/'thumbnail.jpg').write_bytes(thumbnail)
    incident.record={'request_kind':'complete-upload-review','trigger_origin':'reviewer','trigger_time':0,'clip_offset':0,
        'action':None,'observable_events':[{'timestamp_start':0,'timestamp_end':timestamps[-1]['timestamp'],'actors':[],
            'observation':'User-requested full review: '+source.name}],
        'uncertainties':['This is a requested analysis, not an automatic incident detection. The automatic evidence gate did not corroborate an alert.'],
        'supporting_frames':[],'frame_timestamps':timestamps,'files':{'original':'original.mp4','thumbnail':'thumbnail.jpg'},
        'original_sha256':source.original_sha256,'source_sha256':source.original_sha256,'source_file':source.path,
        'model':{'name':'User-requested review','revision':'not-an-automatic-alert'},'lighting_condition':'original_upload',
        'enhancement_used':False,'simulated':False,'review_copies_requested':True,
        'confidence_note':'User-requested review; no AI detection score is asserted',
        'transforms':[{'operation':'Byte-identical uploaded original copied for requested review','original_upload_untouched':True,
            'source_sha256':source.original_sha256,'orientation':'Container display matrix retained; analysis honors its orientation'}]}
    save(incident);save(ReviewAction(incident_id=incident.id,decision='needs_review',note='User requested complete analysis of the uploaded video; this is not an automatic alert.'))
    for kind,enabled,priority in [('review_brief',settings.enable_clip_review,0),('relayvsr',settings.enable_relay_vsr,2),('relift',settings.enable_relift,3)]:
        if enabled:save(Job(incident_id=incident.id,kind=kind,priority=priority))
    return incident


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source_id');args=parser.parse_args()
    init(recover=False)
    print(json.dumps({'incident_id':request_review(args.source_id).id,'origin':'user-requested review','automatic_alert':False}))
