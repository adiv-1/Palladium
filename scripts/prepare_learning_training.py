"""Export a verified snapshot into portable, timestamped multi-frame examples."""
import argparse,hashlib,json
from pathlib import Path
from apps.api.video import digest,read_video
from apps.api.config import settings
from apps.api.db import init
from apps.api.learning import validate_snapshot_current

def prepare(manifest_path,out,check_cancelled=None):
    """Export a fresh verified snapshot, using the same oriented frames as inference."""
    check_cancelled=check_cancelled or (lambda:None)
    check_cancelled()
    source=Path(manifest_path);manifest=json.loads(source.read_text());validate_snapshot_current(manifest)
    out=Path(out)
    if out.exists():raise ValueError('Refusing to overwrite a training frame bundle')
    out.mkdir(parents=True);records=[]
    groups={}
    for e in manifest['examples']:
        check_cancelled()
        video_path=Path(e['video_path'])
        if not video_path.is_absolute():video_path=settings.data_dir/video_path
        if digest(video_path)!=e['video_sha256']:raise ValueError('Original hash changed')
        if e['group_id'] in groups and groups[e['group_id']]!=e['split']:raise ValueError('Split leakage')
        groups[e['group_id']]=e['split']
        label=e['annotation'];start,end=label['start'],label['end'];wanted=[start+(end-start)*f for f in [.05,.35,.65,.95]]
        frames=[];timestamps=[];hashes=[];dest=out/'frames'/e['asset_id'];dest.mkdir(parents=True,exist_ok=True)
        # read_video applies the source display rotation, exactly as live replay.
        # Keep the resulting JPEG bytes and original source times for provenance.
        source_indices=[]
        iterator=read_video(video_path)
        try:
            for frame in iterator:
                check_cancelled()
                if len(frames)==4:break
                if frame.timestamp>=wanted[len(frames)]:
                    path=dest/f'{len(frames)}.jpg';path.write_bytes(frame.jpeg)
                    frames.append(str(path.relative_to(out)));timestamps.append(round(frame.timestamp,4));hashes.append(digest(path));source_indices.append(frame.index)
        finally:iterator.close()
        if digest(video_path)!=e['video_sha256']:raise ValueError('Original changed during frame preparation')
        if len(frames)!=4:raise ValueError('Not enough source frames: '+e['asset_id'])
        normal=label['action']=='ordinary_activity'
        target={'state':'NORMAL' if normal else 'REVIEW_REQUIRED','action':label['action'],'confidence':.8,
            'observable_events':[] if normal else [{'timestamp_start':timestamps[0],'timestamp_end':timestamps[-1],'actors':[],'observation':label['description']}],
            'uncertainties':[] if normal else ['Ownership and authorization cannot be established from video.'] if label['action']=='object_taking' else ['Surrounding context is limited.'],
            'supporting_frames':[] if normal else [0,1,2,3]}
        records.append({'id':e['asset_id'],'group_id':e['group_id'],'video_id':e['provenance'].get('video_id'),
            'split':e['split'],'frames':frames,'timestamps':timestamps,'frame_sha256':hashes,'target':target,
            'original_sha256':e['video_sha256'],'permission':e['permission'],
            'annotation_id':label.get('id'),'annotation_reviewer':label.get('reviewer'),
            'source_frame_indices':source_indices,'frame_orientation':'source display rotation applied by read_video'})
    check_cancelled()
    validate_snapshot_current(manifest)
    raw=''.join(json.dumps(r,sort_keys=True)+'\n' for r in records)
    (out/'records.jsonl').write_text(raw)
    provenance={'source_manifest_sha256':digest(source),'records_sha256':hashlib.sha256(raw.encode()).hexdigest(),
        'source_manifest_created_at':manifest['created_at'],'base_model':'Qwen/Qwen3-VL-4B-Instruct',
        'base_revision':'ebb281ec70b05090aa6165b016eac8ec08e71b17','frames_per_example':4,
        'review_authority':'Per-example snapshot annotation provenance; not institutional confirmation',
        'limits':['Tiny research pilot; unknown base-model pretraining overlap.','Per-example permissions govern training use and redistribution.','No model improvement is assumed before evaluation.']}
    provenance.update(examples=len(records),counts={s:sum(r['split']==s for r in records) for s in ['train','val','test']},frame_orientation='source display rotation applied by read_video')
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2))
    return provenance

def main():
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('--out',default='data/learning/pilot');a=p.parse_args()
    init(recover=False)
    provenance=prepare(a.manifest,a.out)
    print('Prepared',provenance['examples'],'examples',provenance['counts'])

if __name__=='__main__':main()
