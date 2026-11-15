"""Minimal, source-scoped notices for an already authenticated camera socket."""


async def relay_phone_alerts(ws, queue, source_id, simulated=False):
    seen = {}
    while True:
        event = await queue.get()
        source = event.get('payload', {})
        if event.get('type') != 'source' or source.get('id') != source_id:
            continue
        telemetry = source.get('telemetry', {})
        alerts = telemetry.get('review_alerts') or [telemetry.get('review_alert')]
        for alert in alerts:
            if not alert or not alert.get('id'):
                continue
            key = alert['id']
            status = alert.get('status', 'saving_context')
            if seen.get(key) == status:
                continue
            seen[key] = status
            if len(seen) > 32:
                del seen[next(iter(seen))]
            # No coordinates, original footage, review controls or other feeds.
            await ws.send_json({'type': 'phone_alert', 'alert': {
                'id': key, 'status': status, 'simulated': simulated,
                'message': str(alert.get('message') or 'A possible incident needs human review.')[:500],
            }})
