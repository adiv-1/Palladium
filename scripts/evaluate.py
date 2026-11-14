"""Evaluate recorded event predictions. Never substitutes fixture detections for measured inference."""
import argparse
import json

def iou(a,b):
    intersection=max(0,min(a['end'],b['end'])-max(a['start'],b['start']))
    union=max(a['end'],b['end'])-min(a['start'],b['start'])
    return intersection/union if union>0 else 0

def evaluate(truth,predictions,threshold=.3):
    tp=fp=fn=0;overlaps=[];latencies=[];duration=0
    byvideo={x['video']:x for x in predictions['videos']}
    unknown=set(byvideo)-{x['video'] for x in truth['videos']}
    if unknown: raise ValueError(f'Predictions reference unannotated videos: {unknown}')
    for video in truth['videos']:
        duration+=video['duration'];expected=video['events'];used=set()
        for prediction in sorted(byvideo.get(video['video'],{}).get('events',[]),key=lambda x:x.get('detected_at',x['end'])):
            candidates=[(iou(prediction,e),n) for n,e in enumerate(expected) if n not in used]
            score,index=max(candidates,default=(0,-1))
            if score>=threshold:
                tp+=1;used.add(index);overlaps.append(score)
                latencies.append(prediction.get('detected_at',prediction['end'])-expected[index]['start'])
            else: fp+=1
        fn+=len(expected)-len(used)
    return {'true_positives':tp,'false_positives':fp,'false_negatives':fn,
        'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
        'false_alerts_per_hour':fp/(duration/3600) if duration else None,
        'mean_temporal_iou':sum(overlaps)/len(overlaps) if overlaps else None,
        'mean_detection_latency_seconds':sum(latencies)/len(latencies) if latencies else None,
        'annotated_duration_seconds':duration,'iou_threshold':threshold,
        'label':predictions.get('label','unlabeled predictions')}

def main():
    p=argparse.ArgumentParser();p.add_argument('--annotations',required=True);p.add_argument('--predictions',required=True);p.add_argument('--iou',type=float,default=.3);a=p.parse_args()
    with open(a.annotations) as f: truth=json.load(f)
    with open(a.predictions) as f: predictions=json.load(f)
    print(json.dumps(evaluate(truth,predictions,a.iou),indent=2))

if __name__=='__main__':main()
