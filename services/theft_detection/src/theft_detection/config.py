"""Typed configuration. Every threshold that affects an alert lives here, not in code.

Distances and speeds used by the event engine are expressed in *body heights* (the pixel
height of the involved person), so the same thresholds apply to near and far subjects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceConfig(_Model):
    source_id: str = Field(default="test_camera_01", min_length=1, max_length=80)
    uri: str = "0"  # file path, webcam index ("0"), rtsp:// URL or "synthetic:<scenario>"
    # "realtime" drops stale frames like a live camera; "fast" decodes every sampled frame
    # as quickly as possible (deterministic, used for evaluation and benchmarks).
    pace: Literal["realtime", "fast"] = "realtime"
    loop: bool = False
    max_width: int = Field(default=960, ge=160, le=3840)  # frames are downscaled to this width
    # Webcams only: requested capture size (the driver may choose the nearest it supports).
    capture_width: int | None = Field(default=None, ge=160, le=3840)
    capture_height: int | None = Field(default=None, ge=120, le=2160)
    analysis_fps: float = Field(default=10.0, gt=0, le=60)
    queue_size: int = Field(default=2, ge=1, le=64)       # bounded; oldest frame is dropped
    reconnect_attempts: int = Field(default=5, ge=0)
    reconnect_backoff_s: float = Field(default=1.0, ge=0)
    read_timeout_s: float = Field(default=10.0, gt=0)


class DetectorConfig(_Model):
    backend: Literal["yolox_onnx", "color_blob", "none"] = "yolox_onnx"
    model: str = "yolox_tiny"            # key in detection.models.MODEL_REGISTRY or a .onnx path
    weights_dir: str = "weights"
    providers: list[str] = Field(default_factory=lambda: ["CPUExecutionProvider"])
    intra_op_threads: int = Field(default=4, ge=1, le=64)  # caps detector CPU use
    min_score: float = Field(default=0.10, ge=0, le=1)     # everything above goes to the tracker
    nms_iou: float = Field(default=0.45, gt=0, le=1)
    # COCO class name -> tracking category. Small items (phones, wallets, keys) are deliberately
    # absent: a general detector does not see them reliably at CCTV scale.
    classes: dict[str, str] = Field(
        default_factory=lambda: {
            "person": "person",
            "backpack": "bag",
            "handbag": "bag",
            "suitcase": "bag",
            # other things people carry; small ones are rarely detected at camera distance,
            # which is why the action route does not depend on them
            "laptop": "bag",
            "cell phone": "bag",
            "book": "bag",
            "umbrella": "bag",
        }
    )


class PoseConfig(_Model):
    enabled: bool = True             # body keypoints for people who are near another person
    model: str = "rtmpose_t"         # rtmpose_t | rtmpose_s
    threads: int = Field(default=2, ge=1, le=16)
    max_people: int = Field(default=6, ge=2, le=20)   # hard cap per frame
    near: float = 1.8                # body heights between two people for pose to be computed


class TrackerConfig(_Model):
    high_thresh: dict[str, float] = Field(default_factory=lambda: {"person": 0.45, "bag": 0.30})
    new_track_thresh: dict[str, float] = Field(default_factory=lambda: {"person": 0.55, "bag": 0.35})
    low_thresh: float = 0.10
    match_iou: float = 0.20          # minimum IoU for first association
    low_match_iou: float = 0.40      # minimum IoU when matching low-score detections
    bag_iou_buffer: float = 0.5      # bag boxes are enlarged by this ratio before IoU (fast, small)
    person_iou_buffer: float = 0.3   # same for people: a runner outpaces their box width at low FPS
    # Motion gate used to start and to recover tracks when IoU is zero: nobody on foot covers
    # more than about this many body heights per second.
    person_max_speed: float = 4.5
    person_recover_max_gap_s: float = 0.6
    min_hits: int = 2                # a track is reported only after this many matches
    max_lost_s: dict[str, float] = Field(default_factory=lambda: {"person": 1.5, "bag": 2.0})
    bag_recover_sizes: float = 2.0   # bag motion gate, in bag sizes ...
    bag_recover_sizes_per_s: float = 12.0  # ... growing with the time since it was last seen
    bag_recover_max_sizes: float = 8.0
    bag_recover_requires_person: bool = True  # long re-link jumps only next to a person


class EngineConfig(_Model):
    history_s: float = 12.0

    # --- person/object association -------------------------------------------------
    reach: float = 0.18              # person box widened by this many body heights per side
    assoc_min_overlap: float = 0.40  # object-in-reach fraction needed to start an association
    assoc_keep_overlap: float = 0.25 # ... and to keep one (hysteresis)
    assoc_gap_s: float = 0.4
    hold_min_s: float = 1.0          # association time before a person counts as the holder
    holder_grace_s: float = 1.0      # how long a holder may be untracked before we give up
    detach_confirm_s: float = 0.2    # object must stay out of reach this long (or 2 frames)
    late_take_s: float = 1.0         # released object reaching another person within this window

    # --- stage 2: transfer verification ---------------------------------------------
    min_verify_s: float = 0.5        # earliest decision after the candidate onset
    holder_check_frames: int = 4     # frames inspected for a bag still with the prior holder
    veto_min_score: float = 0.30     # detection score needed to count as 'holder still has a bag'
    possession_window_s: float = 5.0 # unobserved transfer: wait this long for the two to separate
    possession_check_frames: int = 8 # ... then look at the prior holder for this many frames
    possession_checks: bool = True   # before/after possession verdicts (False = previous behaviour, for A/B)
    verify_window_s: float = 2.0     # latest decision
    pre_window_s: float = 1.0
    fast_speed: float = 1.3          # body heights / s: faster than brisk walking
    speed_jump: float = 0.8          # required increase of object speed across the transfer
    separation_min: float = 1.4      # object and taker must end this far from the prior holder
    pursuit_separation_min: float = 0.7
    pursuit_speed: float = 1.2
    companion_dist: float = 1.5
    companion_window_s: float = 10.0
    companion_full_s: float = 4.0
    ambiguous_iou: float = 0.6       # person boxes overlapping this much => identities unreliable
    min_observed_frac: float = 0.5   # share of frames in which the object was actually detected
    relinked_min_verify_s: float = 1.0
    review_min_score: float = 0.60
    max_unstable_frac: float = 0.3

    # --- action route (body keypoints; object-agnostic) ---------------------------------
    action_near: float = 1.6         # only pairs whose torsos are this close are examined
    action_hand_dist: float = 0.15   # wrists this close count as hand contact
    action_reach: float = 0.25       # arm extension towards the other person that counts as reaching
    action_linked_dist: float = 0.35 # hands this close with both arms reaching = holding the same thing
    action_pull_apart: float = 0.3   # torsos moving this far apart while linked = a tug
    action_gap_s: float = 0.4        # contact is over after this long without it
    action_window_s: float = 2.0     # the two must separate within this time after contact

    # --- unattended objects -----------------------------------------------------------
    unattended_min_s: float = 3.0
    rest_window_s: float = 1.0
    rest_move_tol: float = 0.15
    pickup_move_min: float = 0.35
    carry_min: float = 1.0
    removal_window_s: float = 4.0
    absent_confirm_s: float = 2.5
    emit_unknown_origin_pickups: bool = False
    # A carried object that stops being detected right after one other person touched it, and
    # that person then leaves: reported as info-level uncertain_interaction (never an alert).
    emit_contact_disappearance: bool = True

    # --- identity bridging for objects ------------------------------------------------
    relink_window_s: float = 1.2
    relink_base: float = 0.6
    relink_per_s: float = 3.5

    # --- zones ----------------------------------------------------------------------
    zone_dwell_s: float = 1.0
    zone_gap_s: float = 0.4
    zone_min_hits: int = 3

    # --- camera motion ----------------------------------------------------------------
    # A frame is "unstable" when the global shift *changes* quickly (shake, a bump) or is
    # extremely fast. A smooth pan is compensated instead of being treated as instability.
    camera_jitter: float = 0.12          # change of global shift, frame diagonals per second
    camera_max_speed: float = 0.45       # global shift, frame diagonals per second
    camera_window_s: float = 1.5

    # --- duplicate suppression --------------------------------------------------------
    cooldown_s: float = 10.0
    zone_cooldown_s: float = 8.0
    emit_info_events: bool = True


class ZoneConfig(_Model):
    zone_id: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_\-]+$")
    # "restricted": a person standing inside raises possible_restricted_area_entry.
    # "monitored":  removal of a resting object from inside is reviewable even when the
    #               person who left it is unknown.
    kind: Literal["restricted", "monitored"]
    polygon: list[tuple[float, float]]  # normalised [0, 1] image coordinates
    active: bool = True
    description: str = ""

    @field_validator("polygon")
    @classmethod
    def _valid_polygon(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(v) < 3:
            raise ValueError("a zone polygon needs at least three vertices")
        for x, y in v:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("zone vertices must be normalised to [0, 1]")
        return v


class EvidenceConfig(_Model):
    enabled: bool = False            # off by default: frames stay in memory only
    directory: str = "data/evidence"
    pre_s: float = Field(default=3.0, ge=0, le=15)
    post_s: float = Field(default=2.0, ge=0, le=15)
    retention_hours: float = Field(default=24.0, gt=0)
    jpeg_quality: int = Field(default=80, ge=30, le=95)
    max_width: int = Field(default=640, ge=160, le=1920)


class WebhookConfig(_Model):
    url: str | None = None           # nothing is ever sent unless a URL is configured
    timeout_s: float = 3.0
    max_retries: int = 3
    queue_size: int = 100
    secret: str | None = None        # optional HMAC-SHA256 signing key
    bearer_token: str | None = None  # sent as "Authorization: Bearer ..." (e.g. SafeSC's app token)


class EventsConfig(_Model):
    max_stored: int = Field(default=500, ge=1)
    jsonl_path: str | None = None    # optional append-only event log (no pixels)
    trials_path: str | None = None   # staged live-trial log, e.g. data/live_trials.jsonl (no pixels)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)


class ApiConfig(_Model):
    host: str = "127.0.0.1"
    port: int = 8077
    token: str | None = None         # when set, required as a Bearer token on /api routes
    demo_dir: str = "data"           # videos offered in the demo UI source picker
    # rtsp:// and http:// sources cannot be started through the API unless this is enabled.
    allow_network_sources: bool = False


class Config(_Model):
    source: SourceConfig = Field(default_factory=SourceConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    pose: PoseConfig = Field(default_factory=PoseConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    zones: list[ZoneConfig] = Field(default_factory=list)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    """Load YAML (optional) and apply a shallow-merged dict of overrides."""
    data: dict = {}
    if path is not None:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: top level of the config must be a mapping")
    if overrides:
        data = _deep_merge(data, overrides)
    return Config.model_validate(data)


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
