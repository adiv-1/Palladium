"""Export a verified snapshot into portable, timestamped multi-frame examples."""
import argparse,hashlib,json
from pathlib import Path
import av
from apps.api.video import digest
from apps.api.config import settings
from apps.api.db import init
from apps.api.learning import validate_snapshot_current

def main():
    p=argparse.ArgumentParser();p.add_argument('manifest');p.add_argument('--out',default='data/learning/pilot');a=p.parse_args()
    source=Path(a.manifest);manifest=json.loads(source.read_text());init(recover=False);validate_snapshot_current(manifest)
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);records=[]
    groups={}
    for e in manifest['examples']:
        video_path=Path(e['video_path'])
        if not video_path.is_absolute():video_path=settings.data_dir/video_path
        if digest(video_path)!=e['video_sha256']:raise ValueError('Original hash changed')
        if e['group_id'] in groups and groups[e['group_id']]!=e['split']:raise ValueError('Split leakage')
        groups[e['group_id']]=e['split']
        label=e['annotation'];start,end=label['start'],label['end'];wanted=[start+(end-start)*f for f in [.05,.35,.65,.95]]
        frames=[];timestamps=[];hashes=[];dest=out/'frames'/e['asset_id'];dest.mkdir(parents=True,exist_ok=True)
        with av.open(str(video_path)) as c:
            for frame in c.decode(video=0):
                t=float(frame.time or 0)
                if len(frames)==4:break
                if t>=wanted[len(frames)]:
                    path=dest/f'{len(frames)}.jpg';frame.to_image().save(path,quality=92)
                    frames.append(str(path.relative_to(out)));timestamps.append(round(t,4));hashes.append(digest(path))
        if len(frames)!=4:raise ValueError('Not enough source frames: '+e['asset_id'])
        normal=label['action']=='ordinary_activity'
        target={'state':'NORMAL' if normal else 'REVIEW_REQUIRED','action':label['action'],'confidence':.8,
            'observable_events':[] if normal else [{'timestamp_start':timestamps[0],'timestamp_end':timestamps[-1],'actors':[],'observation':label['description']}],
            'uncertainties':[] if normal else ['Ownership and authorization cannot be established from video.'] if label['action']=='object_taking' else ['Surrounding context is limited.'],
            'supporting_frames':[] if normal else [0,1,2,3]}
        records.append({'id':e['asset_id'],'group_id':e['group_id'],'video_id':e['provenance'].get('video_id'),
            'split':e['split'],'frames':frames,'timestamps':timestamps,'frame_sha256':hashes,'target':target,
            'original_sha256':e['video_sha256'],'permission':e['permission']})
    raw=''.join(json.dumps(r,sort_keys=True)+'\n' for r in records)
    (out/'records.jsonl').write_text(raw)
    (out/'provenance.json').write_text(json.dumps({'source_manifest_sha256':digest(source),'records_sha256':hashlib.sha256(raw.encode()).hexdigest(),
        'source_manifest_created_at':manifest['created_at'],'base_model':'Qwen/Qwen3-VL-4B-Instruct',
        'base_revision':'ebb281ec70b05090aa6165b016eac8ec08e71b17','frames_per_example':4,
        'review_authority':'UCA research annotations + assistant visual curation; not institutional confirmation',
        'limits':['Tiny research pilot; unknown base-model pretraining overlap.','Research-only source permissions; not cleared for production redistribution.','No model improvement is assumed before evaluation.']},indent=2))
    print('Prepared',len(records),'examples', {s:sum(r['split']==s for r in records) for s in ['train','val','test']})

if __name__=='__main__':main()
