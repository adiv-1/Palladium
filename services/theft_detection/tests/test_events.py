import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from pydantic import ValidationError

from theft_detection import Config, TheftDetector
from theft_detection.config import EvidenceConfig, WebhookConfig, load_config
from theft_detection.events.evidence import EvidenceRecorder
from theft_detection.events.schema import SCHEMA_VERSION, Event, Evidence, event_json_schema
from theft_detection.events.store import EventStore, JsonlSink, WebhookSink
from theft_detection.sim import scenarios
from theft_detection.sim.world import FRAME_H, FRAME_W, ObsParams, observe


def make_event(event_id="evt_0123456789abcdef", **kw):
    base = dict(event_id=event_id, source_id="test_camera_01", event_type="possible_snatching",
                timestamp_utc=datetime.now(timezone.utc), evidence_score=0.78, severity="review",
                observation="A tracked backpack left the reach of person_1 and moved away with person_2.",
                evidence=Evidence(onset_stream_s=3.0, detected_stream_s=3.6, detection_delay_s=0.6),
                model_version="theft_detection/0.1.0 detector=test tracker=bytetrack-np")
    base.update(kw)
    return Event(**base)


def replay(name="P01_runby_snatch", seed=3, on_event=None, cfg=None):
    """Feed a scripted scenario (mock detections) through the public Python interface."""
    sc = scenarios.build(name, seed)
    det = TheftDetector(cfg or Config(), on_event=on_event)
    det.session_key = f"trace:{name}:{seed}"
    det.simulated = True
    if sc.zones:
        det.set_zones(sc.zones)
    rng = np.random.default_rng(seed)
    for k in range(int(sc.duration * 10) + 1):
        dets, gm = observe(sc, k * 0.1, rng, ObsParams(), 0.1)
        det.process_detections(dets, k * 0.1, gm, (FRAME_W, FRAME_H))
    return det


def test_schema_round_trip_and_required_fields():
    ev = make_event()
    data = json.loads(ev.model_dump_json())
    assert data["schema_version"] == SCHEMA_VERSION == "1.0"
    assert data["timestamp_utc"].endswith("Z")
    assert data["review_status"] == "pending" and data["score_calibrated"] is False
    assert Event.model_validate(data) == ev
    required = set(event_json_schema()["required"])
    assert {"event_id", "source_id", "event_type", "timestamp_utc", "evidence_score", "severity",
            "observation", "evidence", "model_version"} <= required


@pytest.mark.parametrize("bad", [
    {"event_id": "evt_1"},
    {"event_type": "theft"},
    {"evidence_score": 1.2},
    {"timestamp_utc": datetime(2026, 11, 14, 15, 0, 0)},                    # naive timestamp
    {"observation": "The suspect stole the bag."},
    {"observation": "A woman handed a bag to a man."},
    {"score_calibrated": True},
    {"severity": "critical"},
    {"unexpected_field": 1},
])
def test_schema_rejects_invalid_or_accusatory_events(bad):
    with pytest.raises(ValidationError):
        make_event(**bad)


def test_store_deduplicates_and_bounds_memory():
    store = EventStore(max_stored=3)
    assert store.add(make_event()) is True
    assert store.add(make_event()) is False and store.duplicates_suppressed == 1
    for i in range(5):
        store.add(make_event(event_id=f"evt_{i:016x}"))
    assert len(store) == 3 and store.get("evt_0123456789abcdef") is None


def test_review_status_handling():
    store = EventStore()
    store.add(make_event())
    assert [e.event_id for e in store.list(status="pending")] == ["evt_0123456789abcdef"]
    ev = store.review("evt_0123456789abcdef", "dismissed", "normal handoff between friends")
    assert ev.review_status == "dismissed" and ev.review.note.startswith("normal")
    assert ev.review.reviewed_at_utc.tzinfo is not None
    assert store.list(status="pending") == [] and len(store.list(status="dismissed")) == 1
    with pytest.raises(KeyError):
        store.review("evt_ffffffffffffffff", "confirmed")


def test_a_failing_listener_does_not_break_detection():
    store = EventStore()
    store.subscribe(lambda e: 1 / 0)
    seen = []
    store.subscribe(seen.append)
    assert store.add(make_event()) and len(seen) == 1 and store.listener_errors == 1


def test_pipeline_emits_one_schema_valid_event_with_stable_id_and_callback():
    seen = []
    det = replay(on_event=seen.append)
    events = det.store.list()
    assert len(events) == 1 and seen == events
    ev = events[0]
    assert ev.event_type == "possible_snatching" and ev.severity == "review" and ev.simulated
    assert ev.review_status == "pending" and 0 < ev.evidence_score <= 1
    assert ev.evidence.detected_stream_s >= ev.evidence.onset_stream_s
    assert abs(ev.evidence.detection_delay_s - (ev.evidence.detected_stream_s - ev.evidence.onset_stream_s)) < 1e-6
    assert abs((datetime.now(timezone.utc) - ev.timestamp_utc).total_seconds()) < 60
    assert ev.evidence.clip_reference is None and ev.evidence.frame_reference is None   # capture is off by default
    assert all(t.ref.startswith("person_") for t in ev.evidence.tracks)
    Event.model_validate(json.loads(ev.model_dump_json()))
    # replaying the same stream yields the same ID, so a consumer can de-duplicate
    assert replay().store.list()[0].event_id == ev.event_id
    assert replay(seed=4).store.list()[0].event_id != ev.event_id


def test_no_event_without_adequate_evidence():
    for name in ("N01_handoff_to_friend", "N10_runs_with_own_bag", "N12_removal_from_unmonitored_spot"):
        assert replay(name).store.list(severity="review") == []


def test_info_events_can_be_switched_off():
    cfg = load_config(None, {"engine": {"emit_info_events": False}})
    for seed in range(6):
        assert all(e.severity == "review" for e in replay(seed=seed, cfg=cfg).store.list())


def test_jsonl_sink_writes_metadata_only(tmp_path):
    cfg = load_config(None, {"events": {"jsonl_path": str(tmp_path / "events.jsonl")}})
    replay(cfg=cfg)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and Event.model_validate_json(lines[0]).event_type == "possible_snatching"


class _Hook(BaseHTTPRequestHandler):
    received: list = []
    fail_first = 0

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if type(self).fail_first > 0:
            type(self).fail_first -= 1
            self.send_response(503)
        else:
            type(self).received.append((dict(self.headers), body))
            self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def hook_server():
    _Hook.received, _Hook.fail_first = [], 0
    server = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/events"
    server.shutdown()


def test_webhook_delivers_signed_events_and_retries(hook_server):
    _Hook.fail_first = 1
    sink = WebhookSink(WebhookConfig(url=hook_server, secret="s3cret", max_retries=2))
    sink(make_event())
    sink.close()
    assert sink.sent == 1 and sink.failed == 0 and len(_Hook.received) == 1
    headers, body = _Hook.received[0]
    assert json.loads(body)["event_id"] == "evt_0123456789abcdef"
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert headers["X-SafeSC-Signature"] == expected and headers["X-SafeSC-Event-Id"] == "evt_0123456789abcdef"


def test_webhook_failure_is_counted_not_raised():
    sink = WebhookSink(WebhookConfig(url="http://127.0.0.1:9/unreachable", max_retries=0, timeout_s=0.5))
    sink(make_event())
    sink.close()
    assert sink.sent == 0 and sink.failed == 1


def test_no_webhook_is_created_unless_configured():
    det = TheftDetector(Config())
    assert det._sinks == []


def test_evidence_capture_is_off_by_default_and_writes_nothing(tmp_path):
    rec = EvidenceRecorder(EvidenceConfig(directory=str(tmp_path / "ev")))
    assert rec.add_frame(0.0, np.zeros((90, 160, 3), np.uint8)) == []
    rec.on_event("evt_0123456789abcdef", 0.0)
    assert rec.flush() == [] and not (tmp_path / "ev").exists()


def test_evidence_capture_saves_a_short_clip_and_purges_it(tmp_path):
    rec = EvidenceRecorder(EvidenceConfig(enabled=True, directory=str(tmp_path / "ev"), pre_s=1.0, post_s=0.5))
    done = []
    for k in range(40):
        if k == 25:
            rec.on_event("evt_0123456789abcdef", k * 0.1)
        done += rec.add_frame(k * 0.1, np.full((90, 160, 3), k * 5, np.uint8))
    assert done == [("evt_0123456789abcdef", "evt_0123456789abcdef.webm", "evt_0123456789abcdef.jpg")]
    files = sorted(p.name for p in (tmp_path / "ev").iterdir())
    assert files == ["evt_0123456789abcdef.jpg", "evt_0123456789abcdef.webm"]
    assert len(rec._ring) <= 12                              # only ~1 s of frames is ever buffered
    assert rec.purge() == 0                                  # within retention
    assert rec.purge(everything=True) == 2 and list((tmp_path / "ev").iterdir()) == []


def test_expired_evidence_is_deleted_on_startup(tmp_path):
    import os
    d = tmp_path / "ev"
    d.mkdir()
    old = d / "evt_aaaaaaaaaaaaaaaa.jpg"
    old.write_bytes(b"x")
    other = d / "notes.txt"
    other.write_text("unrelated")
    os.utime(old, (time.time() - 3 * 3600, time.time() - 3 * 3600))
    EvidenceRecorder(EvidenceConfig(enabled=True, directory=str(d), retention_hours=1.0))
    assert not old.exists() and other.exists()               # only its own expired files are removed


def test_observations_also_pass_safesc_vocabulary_check():
    """Copy of the check in SafeSC's apps/api/schemas.py (copied, not imported: this module
    must not depend on the application). Keep in sync if SafeSC's list changes."""
    import re

    from theft_detection.config import load_config
    from theft_detection.sim.runner import run_trace

    safesc = re.compile(r"\b(criminal|guilty|robbery|burglary|theft|assault|murder|rape|kidnapping|ethnicity|race|"
                        r"suspect|male|female|man|woman|men|women|boy|girl|disabled)\b", re.I)
    cfg = load_config(None, {"engine": {"emit_unknown_origin_pickups": True}})
    types = set()
    for fn in scenarios.ALL:
        for seed in range(3):
            for e in run_trace(fn(np.random.default_rng(90000 + seed)), cfg, seed=seed).events:
                types.add(e.event_type)
                assert not safesc.search(e.observation), e.observation
                assert all(re.fullmatch(r"person_[a-z0-9]+", t["ref"]) for t in e.tracks if t["ref"].startswith("person"))
    assert {"possible_snatching", "suspicious_object_removal", "possible_restricted_area_entry"} <= types
