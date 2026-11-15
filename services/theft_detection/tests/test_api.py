import time

import pytest
from fastapi.testclient import TestClient

from theft_detection.api.app import create_app
from theft_detection.config import load_config
from theft_detection.events.schema import Event


def make_client(tmp_path, **api):
    cfg = load_config(None, {
        "source": {"pace": "fast", "analysis_fps": 10},
        "api": {"demo_dir": str(tmp_path), **api},
        "evidence": {"enabled": True, "directory": str(tmp_path / "evidence"), "pre_s": 1.0, "post_s": 0.5},
    })
    return TestClient(create_app(cfg))


def wait_for(client, predicate, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        status = client.get("/api/v1/status").json()
        if predicate(status):
            return status
        time.sleep(0.1)
    raise AssertionError(f"timed out; last status: {status}")


def test_health_and_idle_status(tmp_path):
    with make_client(tmp_path) as c:
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["schema_version"] == "1.0" and h["pipeline"] == "idle"
        assert c.get("/api/v1/status").json()["status"] == "idle"
        assert c.get("/api/v1/events").json() == []
        assert c.get("/api/v1/decisions").json() == []
        assert "<title>" in c.get("/").text


def test_end_to_end_synthetic_stream_review_flow_and_evidence(tmp_path):
    with make_client(tmp_path) as c:
        r = c.post("/api/v1/source", json={"uri": "synthetic:P01_runby_snatch:3"})
        assert r.status_code == 200 and r.json()["detector"] == "color_blob@synthetic"
        status = wait_for(c, lambda s: s["status"] == "ended")
        assert status["simulated"] is True and status["metrics"]["frames_analysed"] > 50
        assert status["reader"]["frames_dropped"] == 0

        events = c.get("/api/v1/events").json()
        assert len(events) == 1
        ev = Event.model_validate(events[0])                      # the wire format is the public schema
        assert ev.event_type == "possible_snatching" and ev.review_status == "pending" and ev.simulated
        assert ev.evidence.frame_reference == f"{ev.event_id}.jpg"
        img = c.get(f"/api/v1/evidence/{ev.evidence.frame_reference}")
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        assert c.get(f"/api/v1/evidence/{ev.evidence.clip_reference}").status_code == 200

        assert c.get(f"/api/v1/events/{ev.event_id}").json()["event_id"] == ev.event_id
        assert c.get("/api/v1/events", params={"status": "confirmed"}).json() == []
        r = c.post(f"/api/v1/events/{ev.event_id}/review", json={"status": "uncertain", "note": "staged demo"})
        assert r.status_code == 200 and r.json()["review_status"] == "uncertain"
        assert c.get("/api/v1/events", params={"status": "uncertain"}).json()[0]["review"]["note"] == "staged demo"

        # replaying the identical stream must not create a duplicate event
        c.post("/api/v1/source", json={"uri": "synthetic:P01_runby_snatch:3"})
        status = wait_for(c, lambda s: s["status"] == "ended" and s["metrics"]["frames_analysed"] > 50)
        assert len(c.get("/api/v1/events").json()) == 1 and status["duplicates_suppressed"] == 1


def test_rejected_candidates_are_exposed_with_reasons(tmp_path):
    with make_client(tmp_path) as c:
        c.post("/api/v1/source", json={"uri": "synthetic:N01_handoff_to_friend:3"})
        wait_for(c, lambda s: s["status"] == "ended")
        assert c.get("/api/v1/events").json() == []
        decisions = c.get("/api/v1/decisions").json()
        assert [(d["outcome"], d["reason"]) for d in decisions] == [("dismissed", "gradual_transfer")]


def test_clear_failure_responses(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/api/v1/events/evt_0000000000000000").status_code == 404
        assert c.post("/api/v1/events/evt_0000000000000000/review", json={"status": "confirmed"}).status_code == 404
        assert c.post("/api/v1/events/x/review", json={"status": "guilty"}).status_code == 422
        assert c.get("/api/v1/events", params={"status": "bogus"}).status_code == 422
        assert c.post("/api/v1/source", json={"uri": "missing.mp4"}).status_code == 404
        assert c.post("/api/v1/source", json={"uri": "../../secret.mp4"}).status_code == 404
        assert c.post("/api/v1/source", json={"uri": "rtsp://10.0.0.5/stream"}).status_code == 403
        assert c.post("/api/v1/source", json={"uri": "synthetic:P01_runby_snatch", "extra": 1}).status_code == 422
        assert c.get("/api/v1/evidence/..%2F..%2Fpyproject.toml").status_code in (400, 404)
        assert c.get("/api/v1/evidence/evt_0000000000000000.jpg").status_code == 404
        r = c.post("/api/v1/source", json={"uri": "synthetic:nope"})
        assert r.status_code == 200
        status = wait_for(c, lambda s: s["status"] == "error")
        assert "unknown synthetic scenario" in status["detail"]


def test_sources_listing_and_schema_endpoint(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"")
    with make_client(tmp_path) as c:
        s = c.get("/api/v1/sources").json()
        assert s["videos"] == ["clip.mp4"] and "synthetic:P01_runby_snatch" in s["synthetic"]
        assert s["network_sources_allowed"] is False
        assert c.get("/api/v1/schema").json()["properties"]["schema_version"]["const"] == "1.0"


def test_token_is_enforced_when_configured(tmp_path):
    with make_client(tmp_path, token="letmein") as c:
        assert c.get("/health").status_code == 200                       # liveness stays open
        assert c.get("/api/v1/events").status_code == 401
        assert c.get("/api/v1/events", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/v1/events", headers={"Authorization": "Bearer letmein"}).status_code == 200
        assert c.get("/api/v1/status", params={"token": "letmein"}).status_code == 200


def test_live_state_frame_scenarios_and_trial_round_trip(tmp_path):
    with make_client(tmp_path, ) as c:
        assert c.post("/api/v1/trials/start", json={"scenario": "S1_normal_handoff"}).status_code == 409
        assert c.get("/api/v1/frame.jpg").status_code == 404
        assert "S6_abrupt_snatch" in c.get("/api/v1/scenarios").json()
        c.post("/api/v1/source", json={"uri": "synthetic:P01_runby_snatch:3", "pace": "realtime"})
        wait_for(c, lambda s: s["status"] == "running" and (s["metrics"] or {}).get("frames_analysed", 0) > 5)
        assert c.get("/api/v1/frame.jpg").headers["content-type"] == "image/jpeg"
        state = c.get("/api/v1/state").json()["engine"]
        assert set(state) == {"stream_time_s", "objects", "open_candidates", "camera_unstable"}
        assert c.post("/api/v1/trials/start", json={"scenario": "nope"}).status_code == 422
        t = c.post("/api/v1/trials/start", json={"scenario": "S6_abrupt_snatch", "note": "synthetic"}).json()
        assert t["expected"] == "alert"
        assert c.post("/api/v1/trials/start", json={"scenario": "S6_abrupt_snatch"}).status_code == 409
        wait_for(c, lambda s: s["status"] == "ended", timeout=40)
        summary = c.post("/api/v1/trials/stop").json()
        assert summary["verdict"] == "detected" and summary["outcome"] == "alert"
        assert summary["frames_analysed"] > 30 and 0 < summary["bag_detection_coverage"] <= 1
        assert summary["alert_delay_s"] and summary["candidates"][0]["kind"] == "transfer"
        assert c.get("/api/v1/trials").json()[0]["trial_id"] == summary["trial_id"]
        assert c.post("/api/v1/trials/stop").status_code == 409


def test_missing_webcam_is_reported_and_never_replaced(tmp_path):
    from theft_detection.api.app import SourceRequest

    cfg = load_config(None, {"api": {"demo_dir": str(tmp_path)}, "source": {"reconnect_attempts": 0}})
    with TestClient(create_app(cfg, autostart=SourceRequest(uri="9"))) as c:
        status = wait_for(c, lambda s: s["status"] == "error")
        assert "could not open webcam 9" in status["detail"] and status["metrics"]["frames_analysed"] == 0
        assert c.get("/api/v1/frame.jpg").status_code == 404
