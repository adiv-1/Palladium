"""Actual Qwen LoRA optimization and same-prompt, held-out baseline comparison.

No video names/categories appear in model prompts. Training never reads held-out
targets; the deployed model is not changed by this script.
"""
import argparse,hashlib,io,json,random,time
from pathlib import Path
from types import SimpleNamespace

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    import torch
    from PIL import Image
    from transformers import AutoProcessor,Qwen3VLForConditionalGeneration
    from peft import LoraConfig,TaskType,get_peft_model,PeftModel
    from ml.verifier import StructuredVisualVerifier,verification_messages,generation_target
    from apps.api.schemas import Detection
    p=argparse.ArgumentParser();p.add_argument('--data',default='data/learning/pilot');p.add_argument('--checkpoint',default='weights/qwen3vl-4b');p.add_argument('--out',default='data/learning/candidate');p.add_argument('--steps',type=int,default=60)
    p.add_argument('--resume-adapter',default='');p.add_argument('--learning-rate',type=float,default=1e-4);a=p.parse_args()
    torch.manual_seed(42);random.seed(42);root=Path(a.data);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    records=[json.loads(line) for line in (root/'records.jsonl').read_text().splitlines()];provenance=json.loads((root/'provenance.json').read_text())
    if sha(root/'records.jsonl')!=provenance['records_sha256']:raise ValueError('Records hash mismatch')
    groups={}
    for r in records:
        if r['group_id'] in groups and groups[r['group_id']]!=r['split']:raise ValueError('Group leakage')
        groups[r['group_id']]=r['split'];Detection.model_validate(r['target'])
        for path,expected in zip(r['frames'],r['frame_sha256']):
            if sha(root/path)!=expected:raise ValueError('Frame hash changed')
    train=[r for r in records if r['split']=='train'];held=[r for r in records if r['split'] in ['val','test']]
    if not train or not any(r['split']=='val' for r in held) or not any(r['split']=='test' for r in held):raise ValueError('Three separate splits are required')
    started=time.monotonic();print('Loading pinned base model',flush=True)
    processor=AutoProcessor.from_pretrained(a.checkpoint,min_pixels=56*56,max_pixels=336*336)
    model=Qwen3VLForConditionalGeneration.from_pretrained(a.checkpoint,dtype=torch.bfloat16,attn_implementation='sdpa',device_map='cuda:0')
    parent_report=None
    if a.resume_adapter:
        from ml.verifier import validate_adapter
        parent_report=validate_adapter(a.resume_adapter,provenance['base_revision'],research_mode=True)
        model=PeftModel.from_pretrained(model,a.resume_adapter,is_trainable=True)
    model.config.use_cache=True
    verifier=StructuredVisualVerifier.__new__(StructuredVisualVerifier);verifier.torch=torch;verifier.processor=processor;verifier.model=model;verifier.revision=provenance['base_revision'];verifier.adapter='';verifier.research_mode=False;verifier.last_audit={}
    def frames(r):return [SimpleNamespace(index=i,timestamp=t,jpeg=(root/f).read_bytes()) for i,f,t in zip(r.get('frame_indices',range(len(r['frames']))),r['frames'],r.get('model_timestamps',r['timestamps']))]
    def evaluate(label):
        results=[];model.eval()
        for r in held:
            t=time.monotonic()
            try:
                predicted=verifier.verify_frames(frames(r));error=None;prediction=predicted.model_dump()
            except Exception as e:prediction=None;error=str(e)
            result={'id':r['id'],'split':r['split'],'expected':r['target']['action'],'prediction':prediction,'error':error,'latency_seconds':time.monotonic()-t}
            results.append(result);print(label,r['split'],r['id'][:8],r['target']['action'],'->',prediction and prediction.get('action'),error or '',flush=True)
        return results
    def metrics(results,split):
        rows=[r for r in results if r['split']==split];positive=[r for r in rows if r['expected']!='ordinary_activity'];negative=[r for r in rows if r['expected']=='ordinary_activity']
        alert=lambda r:bool(r['prediction'] and r['prediction']['state'] in ['WATCH','REVIEW_REQUIRED','HIGH_PRIORITY_REVIEW'])
        hits=sum(alert(r) for r in positive);false=sum(alert(r) for r in negative)
        return {'examples':len(rows),'positive_examples':len(positive),'normal_examples':len(negative),
            'review_recall':hits/len(positive) if positive else None,'missed_positive_examples':len(positive)-hits,
            'false_positive_examples':false,'false_positive_rate':false/len(negative) if negative else None,
            'action_accuracy':sum(bool(r['prediction'] and r['prediction'].get('action')==r['expected']) for r in rows)/len(rows),
            'invalid_outputs':sum(r['prediction'] is None for r in rows),
            'per_action_recall':{action:sum(bool(r['prediction'] and r['prediction'].get('action')==action) for r in rows if r['expected']==action)/sum(r['expected']==action for r in rows) for action in sorted({r['expected'] for r in rows})}}
    baseline=evaluate('BASELINE');(out/'baseline.json').write_text(json.dumps(baseline,indent=2))
    # Only language attention adapters train; the vision encoder and base weights
    # remain frozen. The optimizer consumes training records exclusively.
    if not a.resume_adapter:model=get_peft_model(model,LoraConfig(task_type=TaskType.CAUSAL_LM,r=8,lora_alpha=16,lora_dropout=.05,target_modules=['q_proj','v_proj']))
    model.config.use_cache=False
    verifier.model=model
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False});model.enable_input_require_grads();model.train()
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=a.learning_rate,weight_decay=.01)
    trainable=sum(p.numel() for p in model.parameters() if p.requires_grad);print('Trainable LoRA parameters',trainable,flush=True)
    cached=[]
    for r in train:
        fs=frames(r);messages,_=verification_messages(fs);prompt=processor.apply_chat_template(messages,tokenize=False,add_generation_prompt=True)
        images=[Image.open(io.BytesIO(f.jpeg)).convert('RGB') for f in fs]
        target=json.dumps(generation_target(r['target']),separators=(',',':'))+'<|im_end|>'
        inputs=processor(text=[prompt+target],images=images,return_tensors='pt')
        prefix=processor(text=[prompt],images=images,return_tensors='pt')['input_ids'].shape[-1]
        labels=inputs['input_ids'].clone();labels[:,:prefix]=-100;inputs['labels']=labels
        cached.append(inputs)
    losses=[];optimizer.zero_grad(set_to_none=True);training_started=time.monotonic()
    for step in range(a.steps):
        micro=[]
        for _ in range(2):
            batch={k:v.to('cuda:0') for k,v in random.choice(cached).items()}
            with torch.autocast('cuda',dtype=torch.bfloat16):loss=model(**batch).loss
            if not torch.isfinite(loss):raise RuntimeError('Non-finite training loss')
            (loss/2).backward();micro.append(float(loss.detach()))
        torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step();optimizer.zero_grad(set_to_none=True)
        losses.append(sum(micro)/len(micro))
        print('STEP',step+1,'LOSS',round(losses[-1],5),'ELAPSED',round(time.monotonic()-training_started,1),flush=True)
        if (step+1)%20==0:model.save_pretrained(out/'checkpoint')
    optimization_seconds=time.monotonic()-training_started
    adapter=out/'adapter';model.save_pretrained(adapter);processor.save_pretrained(adapter)
    trained_norm=sum(float(p.detach().float().norm()) for name,p in model.named_parameters() if 'lora_B' in name)
    model.eval();model.gradient_checkpointing_disable();model.config.use_cache=True
    candidate=evaluate('CANDIDATE')
    bm={s:metrics(baseline,s) for s in ['val','test']};cm={s:metrics(candidate,s) for s in ['val','test']}
    # Small pilot: require strictly better action recognition, no lost positive
    # review recall, no added normal-clip false positives, and valid JSON.
    nonregression=all(cm[s]['action_accuracy']>=bm[s]['action_accuracy'] and cm[s]['review_recall']>=bm[s]['review_recall'] and cm[s]['false_positive_examples']<=bm[s]['false_positive_examples'] and cm[s]['invalid_outputs']==0 for s in ['val','test'])
    passed=nonregression and any(cm[s]['action_accuracy']>bm[s]['action_accuracy'] or cm[s]['false_positive_examples']<bm[s]['false_positive_examples'] for s in ['val','test'])
    report={'kind':'research LoRA pilot','base_model':provenance['base_model'],'base_revision':provenance['base_revision'],
        'dataset':provenance,'training_examples':len(train),'steps':a.steps,'seed':42,'trainable_parameters':trainable,
        'unique_training_videos':len({r['group_id'] for r in train}),'learning_rate':a.learning_rate,
        'baseline_adapter_sha256':parent_report['adapter_sha256'] if parent_report else None,
        'losses':losses,'trained_lora_b_norm':trained_norm,'optimizer_updates_performed':True,
        'runtime_seconds':time.monotonic()-started,'optimization_seconds':optimization_seconds,
        'peak_allocated_vram_mb':torch.cuda.max_memory_allocated()/1024**2,'baseline':bm,'candidate':cm,
        'promotion_passed':passed and not(parent_report and not parent_report.get('promotion_passed')),
        'improved_over_parent':passed,'nonregression':nonregression,'deployed':False,
        'adapter_sha256':sha(adapter/'adapter_model.safetensors'),'adapter_config_sha256':sha(adapter/'adapter_config.json'),
        'inference_max_pixels':336*336,'generation_schema_version':'explicit-times-v1',
        'limitations':['Tiny academic research sample; not production validation.','Clips were prelocalized; this does not measure live temporal-gate recall or false alerts per hour.','Unknown foundation pretraining overlap.','Research-only source footage; no campus-footage rights inferred.'],
        'candidate_predictions':candidate}
    (out/'evaluation.json').write_text(json.dumps(report,indent=2));(adapter/'evaluation.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ['baseline','candidate','promotion_passed','runtime_seconds','peak_allocated_vram_mb']},indent=2),flush=True)

if __name__=='__main__':main()
