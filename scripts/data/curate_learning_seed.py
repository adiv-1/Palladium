"""Materialize the explicitly reviewed research pilot; never official labels.

Descriptions are supervised labels, not runtime rules. Video IDs and captions
are excluded from model inputs. Ambiguous candidate windows remain pending.
"""
import json
from pathlib import Path
from apps.api.db import init,save
from apps.api.learning import (register_local,LearningPermission,AnnotationInput,annotate,
    latest_annotations,permission_for,build_snapshot)

CONTACT={
 'Assault018_x264':'A person approaches another person, who falls following contact.',
 'Assault024_x264':'A person strikes another person and the other person falls.',
 'Assault020_x264':'One person pushes and strikes another person in a small group.',
 'Assault012_x264':'A person runs toward another person and pushes them to the ground.',
 'Assault026_x264':'A standing person repeatedly strikes a seated person.',
 'Assault025_x264':'People make forceful physical contact in a crowd.',
 'Assault050_x264':'One person knocks another person down and makes repeated physical contact.',
 'Assault044_x264':'Several people strike or push a person in a crowded doorway.',
 'Assault052_x264':'A person runs toward a group and pushes people down.',
 'Assault038_x264':'One person strikes another person, who falls to the ground.',
 'Assault039_x264':'One person repeatedly strikes another person near a counter.',
 'Assault029_x264':'Two standing people make repeated kicking motions toward a person on the ground.',
 'Assault031_x264':'People push and strike each other in a crowded area.',
}
TAKING={
 'Stealing055_x264':'A person removes a small object from a drawer and moves it toward their clothing.',
 'Stealing074_x264':'A person reaches inside a vehicle and removes an item.',
 'Stealing024_x264':'A person reaches behind a seated person and removes a bag.',
 'Robbery023_x264':'A person collects items from a display counter into a bag.',
 'Robbery082_x264':'People reach toward another person and remove items from their hands.',
 'Robbery011_x264':'A person reaches into the cashier area and collects items into a bag.',
 'Stealing097_x264':'A person opens a vehicle door, removes an item, and moves away.',
 'Robbery123_x264':'A person reaches across a counter and pulls at a computer while another person holds it.',
 'Stealing081_x264':'A person removes an item from a vehicle and moves away.',
 'Robbery089_x264':'A person pulls at another person\'s bag, and the other person falls.',
 'Robbery102_x264':'Two people pull on the same bag in a courtyard.',
}
NEGATIVES={'Robbery072_x264','Robbery105_x264'}

def main():
    init(recover=False);root=Path('data/learning-research');selection=json.loads((root/'selection.json').read_text())['examples']
    review=[]
    for item in selection:
        video=item['video_id'];description=CONTACT.get(video) or TAKING.get(video) or ''
        negative=video.startswith('Normal') or video in NEGATIVES
        approved=bool(description or negative)
        action='ordinary_activity' if negative else 'physical_contact' if video in CONTACT else 'object_taking' if video in TAKING else 'insufficient_visibility'
        provenance={k:item[k] for k in ['video_id','source_url','annotation_url','archive_member','video_sha256','annotation_interval']}
        provenance.update(official_split=item['split'],rights='academic_research_only',
            annotation_revision='e478cb5b2277c6b7cf6a1dc4691021592003e216',
            review_method='Published UCA human annotations plus assistant contact-sheet visual curation; not a USC official or independent adjudication',
            curation_note='Selected pre-event ordinary window' if video in NEGATIVES else 'Ambiguous or off-target window excluded' if not approved else 'Visible action pilot label',
            potential_foundation_pretraining_overlap='unknown')
        asset=register_local(item['video_path'],'UCF research · '+video,'ucf:'+video,'research',video,provenance)
        end=min(item['end'],asset.duration-.001)
        start=min(item['start'],max(0,end-.1))
        if permission_for('asset:'+asset.id)=='unknown':save(LearningPermission(subject='asset:'+asset.id,permission='academic_research',note='Official UCF research download; UCA academic/research-only usage notice. Not cleared for production redistribution.'))
        current=latest_annotations().get(asset.id)
        if not current:
            annotate(asset,AnnotationInput(action=action,start=start,end=end,basis='visible' if approved else 'uncertain',status='approved' if approved else 'pending',
                description=description,note='Research pilot curation; ordinary interval inside an anomalous-category recording' if video in NEGATIVES else 'UCA source annotation; assistant visually checked selected contact sheet, not legal ground truth'),
                reviewer='UCA annotation + assistant visual curation (research)')
        review.append({'video_id':video,'split':item['split'],'start':start,'end':end,'action':action,'eligible':approved,'description':description,'asset_id':asset.id})
    result=build_snapshot();(root/'curation.json').write_text(json.dumps({'examples':review,'snapshot_id':result.id,'snapshot_path':result.path,'summary':result.record},indent=2))
    print(json.dumps(result.model_dump(),indent=2))

if __name__=='__main__':main()
