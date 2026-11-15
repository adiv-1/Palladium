"""Opt-in browser geolocation; coordinates never influence video inference.

Only the paired capture WebSocket supplies updates. No IP lookup, map request,
reverse geocoding, or inference about location occurs here.
"""
from collections import deque
from copy import deepcopy
import math
import time

STATUSES = {'active', 'denied', 'unavailable', 'disabled'}
FIX_INTERVAL_SECONDS = 5
MAX_FIX_AGE_SECONDS = 60
MAX_MEASUREMENT_AGE_MS = 5*60*1000
MAX_FUTURE_SKEW_MS = 60*1000


def number(value):
    if type(value) not in (int,float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class Geolocation:
    def __init__(self):
        self.current = {'status': 'disabled', 'provenance': 'browser_permission'}
        self.history = deque(maxlen=90)
        self.last_fix_monotonic = None

    def update(self, payload, stream_timestamp, *, now_ms=None, monotonic=None):
        now_ms = time.time()*1000 if now_ms is None else now_ms
        monotonic = time.monotonic() if monotonic is None else monotonic
        status = payload.get('status') if isinstance(payload, dict) else None
        if not isinstance(status,str) or status not in STATUSES:
            return self.ack(False, 'invalid_status')
        stream_timestamp = max(0., float(stream_timestamp))
        if status != 'active':
            # Clear live coordinates immediately, but retain a bounded permission
            # timeline so delayed video analysis can geotag an earlier event.
            changed = self.current['status'] != status
            self.current = {'status': status, 'provenance': 'browser_permission',
                'stream_timestamp': stream_timestamp,'received_stream_timestamp': stream_timestamp}
            self.history.append(deepcopy(self.current))
            self.last_fix_monotonic = None
            return self.ack(changed, None if changed else 'unchanged')
        latitude, longitude = payload.get('latitude'), payload.get('longitude')
        accuracy, measured = payload.get('accuracy_m'), payload.get('measured_at')
        if not all(number(v) for v in (latitude, longitude, accuracy, measured)):
            return self.ack(False, 'invalid_fix')
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and 0 <= accuracy <= 100000):
            return self.ack(False, 'invalid_fix')
        if measured < now_ms-MAX_MEASUREMENT_AGE_MS or measured > now_ms+MAX_FUTURE_SKEW_MS:
            return self.ack(False, 'measurement_out_of_date')
        if self.current['status'] == 'active' and self.last_fix_monotonic is not None and monotonic-self.last_fix_monotonic < FIX_INTERVAL_SECONDS:
            return self.ack(False, 'rate_limited')
        if self.current['status'] == 'active' and measured < self.current['measured_at']:
            return self.ack(False, 'out_of_order_fix')
        # Keep source receipt time separate from the measurement's age. A cached
        # fix received after an event cannot be used to backfill that event.
        age = max(0., (now_ms-measured)/1000)
        fix = {'status': 'active', 'latitude': float(latitude), 'longitude': float(longitude),
            'accuracy_m': float(accuracy), 'measured_at': float(measured),
            'stream_timestamp': stream_timestamp, 'received_stream_timestamp': stream_timestamp,
            'measured_stream_timestamp': stream_timestamp-age,
            'provenance': 'browser_permission'}
        self.current = fix
        self.history.append(deepcopy(fix))
        self.last_fix_monotonic = monotonic
        return self.ack(True)

    def ack(self, accepted, reason=None):
        result = {'type': 'geolocation', 'accepted': accepted, **deepcopy(self.current)}
        if reason:
            result['reason'] = reason
        return result

    def clear(self):
        self.current = {'status': 'disabled', 'provenance': 'browser_permission'}
        self.history.clear()
        self.last_fix_monotonic = None

    def snapshot(self, trigger):
        if not self.history:
            return {'status': self.current['status'], 'provenance': 'browser_permission'}
        preceding = [(i,item) for i,item in enumerate(self.history)
            if item['received_stream_timestamp'] <= trigger]
        if not preceding:
            return {'status': 'unavailable', 'reason': 'No permissioned fix preceding this event',
                'provenance': 'browser_permission'}
        # Latest receipt wins; insertion order resolves a fix and revocation
        # received between the same two camera frames in favor of the later event.
        _, selected = max(preceding, key=lambda pair: (pair[1]['received_stream_timestamp'],pair[0]))
        if selected['status'] != 'active':
            return {'status': selected['status'], 'provenance': 'browser_permission'}
        age = max(0., trigger-selected['measured_stream_timestamp'])
        if age > MAX_FIX_AGE_SECONDS:
            return {'status': 'stale', 'age_seconds': round(age, 3),
                'reason': 'Location fix was more than 60 seconds old at the event',
                'provenance': 'browser_permission'}
        return {**deepcopy(selected), 'age_seconds': round(age, 3),
            'notice': 'Browser-reported camera position and accuracy; not a verified incident location'}
