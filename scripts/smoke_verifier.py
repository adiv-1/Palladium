"""Real visual-verifier check on a benign repeated public image, with MOSS resident."""
import json
from pathlib import Path
from apps.api.video import Frame
from ml.verifier import StructuredVisualVerifier

image=Path('data/smoke/bus.jpg').read_bytes()
model=StructuredVisualVerifier('weights/qwen3-vl-4b')
result=model.verify_frames([Frame(float(i),i,image) for i in range(4)],'A bus is parked. No visible change.')
passed=result.state in ['NORMAL','WATCH']
report={'status':'passed' if passed else 'failed','observation':result.model_dump(),'audit':model.last_audit,
    'note':'Benign schema/vision check, not an incident accuracy benchmark'}
Path('data/smoke/verifier-metrics.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2),flush=True)
assert passed,'False positive on the benign static test'
