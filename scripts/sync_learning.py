"""Transfer learning metadata without overwriting live incident/source tables.

Copy data/learning/ assets/snapshots separately. This script deliberately does
not transfer provider credentials, main SQLite databases, or model weights.
"""
import argparse,json
from pathlib import Path
from apps.api.config import settings
from apps.api.db import init,rows,get,save
from apps.api.learning import LearningAsset,LearningPermission,LearningAnnotation,DatasetSnapshot,LearningRun,folder

MODELS=[LearningAsset,LearningPermission,LearningAnnotation,DatasetSnapshot,LearningRun]

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['export','import']);p.add_argument('file');a=p.parse_args();init(recover=False)
    if a.action=='export':
        result={m.__name__:[r.model_dump() for r in rows(m)] for m in MODELS}
        for r in result['LearningAsset']:r['path']='assets/'+Path(r['path']).name
        for r in result['DatasetSnapshot']:r['path']='snapshots/'+Path(r['path']).name
        Path(a.file).write_text(json.dumps(result,indent=2));print('Exported learning metadata');return
    records=json.loads(Path(a.file).read_text());base=folder().resolve()
    for model in MODELS:
        for r in records.get(model.__name__,[]):
            if 'path' in r:
                path=(base/r['path']).resolve()
                if base not in path.parents:raise ValueError('Import path escaped learning storage')
                r['path']=str(path)
            previous=get(model,r['id'])
            if previous:
                if model in [LearningAsset,DatasetSnapshot] and previous.sha256!=r['sha256']:raise ValueError('Immutable record hash conflict')
                # Runs are mutable progress records; annotation/permission events
                # with existing IDs are never overwritten by an import.
                if model==LearningRun:
                    previous.status=r['status'];previous.record=r['record'];save(previous)
            else:save(model(**r))
    print('Imported learning metadata; live source/incident tables untouched')

if __name__=='__main__':main()
