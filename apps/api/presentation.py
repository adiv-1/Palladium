"""Read-only processing summaries for the incident queue."""

def processing_summary(incident,jobs):
    reviews=[job for job in jobs if job.kind=='review_brief']
    statuses={job.status for job in reviews}
    status=('running' if 'running' in statuses else 'queued' if 'queued' in statuses else
        'completed' if incident.record.get('clip_review',{}).get('report') else
        'failed' if 'failed' in statuses else 'not_requested')
    return {'review_brief':{'status':status},
        **{status:sum(job.status==status for job in jobs) for status in ('queued','running','failed')}}
