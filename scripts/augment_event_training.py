"""Auditable training-only image/time augmentation; held-out footage is copied unchanged."""
import argparse,copy,hashlib,json,random,shutil
from pathlib import Path
from PIL import Image,ImageEnhance,ImageOps
from apps.api.video import digest

def augment(source,out):
    source=Path(source);out=Path(out)
    if out.exists():raise ValueError('Refusing to overwrite a training bundle')
    records=[json.loads(x) for x in (source/'records.jsonl').read_text().splitlines()]
    # Re-training an augmented experiment must not multiply its derived variants again.
    records=[r for r in records if r['id']==r.get('asset_id',r['id'])]
    original=json.loads((source/'provenance.json').read_text())
    if digest(source/'records.jsonl')!=original['records_sha256']:raise ValueError('Parent bundle hash mismatch')
    out.mkdir(parents=True);result=[];rng=random.Random(20261114)
    for r in records:
        for path,expected in zip(r['frames'],r['frame_sha256']):
            if digest(source/path)!=expected:raise ValueError('Source frame hash mismatch')
        for variant in range(4 if r['split']=='train' else 1):
            item=copy.deepcopy(r);item['asset_id']=r['id'];item['id']=r['id']+(f'-aug{variant}' if variant else '')
            dest=out/'frames'/item['id'];dest.mkdir(parents=True)
            item['frames']=[];item['frame_sha256']=[]
            brightness=[1,.7,.9,1.1][variant];flip=variant==3
            for i,path in enumerate(r['frames']):
                target=dest/f'{i}.jpg'
                if not variant:shutil.copyfile(source/path,target)
                else:
                    image=Image.open(source/path).convert('RGB')
                    if flip:image=ImageOps.mirror(image)
                    ImageEnhance.Brightness(image).enhance(brightness).save(target,quality=92)
                item['frames'].append(str(target.relative_to(out)));item['frame_sha256'].append(digest(target))
            if variant:
                offset=round(rng.uniform(50,5000),3)-r['timestamps'][0]
                ids=sorted(rng.sample(range(100,5000),len(r['frames'])))
                item['frame_indices']=ids;item['model_timestamps']=[round(t+offset,4) for t in r['timestamps']]
                target=item['target']
                target['supporting_frames']=[ids[i] for i in target['supporting_frames']]
                for e in target['observable_events']:
                    e['timestamp_start']=round(e['timestamp_start']+offset,4);e['timestamp_end']=round(e['timestamp_end']+offset,4)
            item['augmentation']={'brightness':brightness,'horizontal_flip':flip,'source_asset_id':r['id'],
                'times_in_timestamps':'original video seconds','model_time_augmented':bool(variant)}
            result.append(item)
    raw=''.join(json.dumps(r,sort_keys=True)+'\n' for r in result);(out/'records.jsonl').write_text(raw)
    provenance={**original,'records_sha256':hashlib.sha256(raw.encode()).hexdigest(),
        'parent_records_sha256':original['records_sha256'],'augmentation_seed':20261114,
        'training_source_videos':len([r for r in records if r['split']=='train']),
        'training_examples_including_variants':len([r for r in result if r['split']=='train']),
        'held_out_unchanged':True,'augmentation_policy':'Train only: mild brightness, consistent horizontal flip, source-index and timestamp rebasing. No temporal cropping or invented action labels.'}
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2))
    return provenance

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');a=p.parse_args()
    print(json.dumps(augment(a.source,a.output),indent=2))
