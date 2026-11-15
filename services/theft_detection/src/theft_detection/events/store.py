"""In-memory event store with de-duplication, human review state and outbound sinks."""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..config import WebhookConfig
from .schema import Event, ReviewRecord

Listener = Callable[[Event], None]


class EventStore:
    """Thread-safe, bounded. Adding an event whose ID already exists is a no-op."""

    def __init__(self, max_stored: int = 500) -> None:
        self._events: OrderedDict[str, Event] = OrderedDict()
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self.max_stored = max_stored
        self.duplicates_suppressed = 0
        self.listener_errors = 0

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def add(self, event: Event) -> bool:
        with self._lock:
            if event.event_id in self._events:
                self.duplicates_suppressed += 1
                return False
            self._events[event.event_id] = event
            while len(self._events) > self.max_stored:
                self._events.popitem(last=False)
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                self.listener_errors += 1   # a broken consumer must not stop detection
        return True

    def get(self, event_id: str) -> Event | None:
        with self._lock:
            return self._events.get(event_id)

    def list(self, status: str | None = None, severity: str | None = None,
             since: datetime | None = None, limit: int = 100) -> list[Event]:
        with self._lock:
            items = list(self._events.values())
        if status:
            items = [e for e in items if e.review_status == status]
        if severity:
            items = [e for e in items if e.severity == severity]
        if since:
            items = [e for e in items if e.timestamp_utc > since]
        return items[-limit:][::-1]

    def review(self, event_id: str, status: str, note: str = "") -> Event:
        with self._lock:
            ev = self._events.get(event_id)
            if ev is None:
                raise KeyError(event_id)
            updated = ev.model_copy(update={
                "review_status": status,
                "review": ReviewRecord(status=status, note=note, reviewed_at_utc=datetime.now(timezone.utc)),
            })
            self._events[event_id] = updated
            return updated

    def set_evidence(self, event_id: str, clip: str | None, frame: str | None) -> None:
        with self._lock:
            ev = self._events.get(event_id)
            if ev is None:
                return
            evidence = ev.evidence.model_copy(update={"clip_reference": clip, "frame_reference": frame})
            self._events[event_id] = ev.model_copy(update={"evidence": evidence})

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


class JsonlSink:
    """Append-only event log. Contains event metadata only - never pixels."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, event: Event) -> None:
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(event.model_dump_json() + "\n")


class WebhookSink:
    """POSTs each event to a configured URL from a background thread.

    Bounded queue, bounded retries, optional HMAC signature. Nothing is sent unless the
    integrator configures a URL; the detector itself never contacts any external party.
    """

    def __init__(self, cfg: WebhookConfig) -> None:
        if not cfg.url:
            raise ValueError("webhook url is not configured")
        self.cfg = cfg
        self._queue: queue.Queue[Event | None] = queue.Queue(maxsize=cfg.queue_size)
        self.sent = 0
        self.failed = 0
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="webhook-sink", daemon=True)
        self._thread.start()

    def __call__(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def close(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        import httpx

        with httpx.Client(timeout=self.cfg.timeout_s) as client:
            while True:
                event = self._queue.get()
                if event is None:
                    return
                body = event.model_dump_json().encode("utf-8")
                headers = {"Content-Type": "application/json", "X-SafeSC-Event-Id": event.event_id}
                if self.cfg.bearer_token:
                    headers["Authorization"] = f"Bearer {self.cfg.bearer_token}"
                if self.cfg.secret:
                    sig = hmac.new(self.cfg.secret.encode(), body, hashlib.sha256).hexdigest()
                    headers["X-SafeSC-Signature"] = f"sha256={sig}"
                for attempt in range(self.cfg.max_retries + 1):
                    try:
                        resp = client.post(self.cfg.url, content=body, headers=headers)
                        if resp.status_code < 300:
                            self.sent += 1
                            break
                        if resp.status_code < 500 and resp.status_code != 429:
                            self.failed += 1      # the consumer rejected it; retrying will not help
                            break
                    except httpx.HTTPError:
                        pass
                    if attempt == self.cfg.max_retries:
                        self.failed += 1
                    else:
                        time.sleep(min(0.5 * 2 ** attempt, 5.0))


def dumps(event: Event) -> str:
    return json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
