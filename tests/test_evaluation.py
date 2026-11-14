from scripts.evaluate import evaluate

def test_one_to_one_matching_penalizes_duplicates_and_missing_events():
    truth={'videos':[{'video':'a','duration':3600,'events':[{'start':1,'end':3},{'start':10,'end':12}]}]}
    predictions={'videos':[{'video':'a','events':[{'start':1,'end':3,'detected_at':2},{'start':1,'end':3,'detected_at':2.5}]}]}
    result=evaluate(truth,predictions)
    assert result['precision']==.5
    assert result['recall']==.5
    assert result['false_alerts_per_hour']==1
    assert result['mean_detection_latency_seconds']==1
