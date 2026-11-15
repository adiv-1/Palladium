"""Versioned public event contract (schema 1.0).

This is the only thing SafeSC needs to understand. It is deliberately conservative:

* ``event_type`` names an *observed pattern* with an explicit "possible"/"suspicious" hedge;
* ``observation`` is a plain description of what moved, validated against a vocabulary that
  would turn it into an accusation or a personal description;
* ``evidence_score`` is a heuristic evidence-quality score. ``score_calibrated`` is always
  ``False`` until someone calibrates it against labelled footage - it is not a probability.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"

EventType = Literal[
    "possible_snatching",
    "suspicious_object_removal",
    "rapid_object_transfer",
    "possible_restricted_area_entry",
    "uncertain_interaction",
]
Severity = Literal["info", "review"]
ReviewStatus = Literal["pending", "confirmed", "dismissed", "uncertain"]

# Words the detector must never put in an observation: legal conclusions and personal traits.
_FORBIDDEN = re.compile(
    r"\b(criminal|crime|guilty|robbery|robber|burglary|burglar|theft|thief|thieves|stole|stolen|"
    r"steal|stealing|assault|suspect|perpetrator|offender|victim|owner|ethnicity|race|skin|"
    r"male|female|man|woman|men|women|boy|girl|disabled|age|aged)\b",
    re.IGNORECASE,
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrackRef(_Model):
    ref: str = Field(pattern=r"^(person|object)_[0-9]+$")   # ephemeral, camera-local, anonymous
    role: str = Field(max_length=40)


class Evidence(_Model):
    clip_reference: str | None = None    # set only when evidence capture is enabled
    frame_reference: str | None = None
    onset_stream_s: float = Field(ge=0)        # stream time when the observed change began
    detected_stream_s: float = Field(ge=0)     # stream time when the alert was raised
    detection_delay_s: float = Field(ge=0)     # detected - onset (includes verification window)
    processing_lag_s: float | None = None      # wall-clock capture-to-alert lag of that frame
    features: dict[str, float] = Field(default_factory=dict)
    tracks: list[TrackRef] = Field(default_factory=list, max_length=20)
    object_label: str | None = None
    object_ref: str | None = None
    zone_id: str | None = None


class ReviewRecord(_Model):
    status: ReviewStatus
    note: str = Field(default="", max_length=2000)
    reviewed_at_utc: datetime


class Event(_Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    event_id: str = Field(pattern=r"^evt_[0-9a-f]{16}$")
    source_id: str = Field(min_length=1, max_length=80)
    event_type: EventType
    timestamp_utc: datetime
    evidence_score: float = Field(ge=0, le=1)
    score_calibrated: Literal[False] = False
    severity: Severity
    observation: str = Field(min_length=3, max_length=1000)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    evidence: Evidence
    model_version: str = Field(min_length=1, max_length=200)
    review_status: ReviewStatus = "pending"
    review: ReviewRecord | None = None
    simulated: bool = False   # True when the footage or the detections were synthetic

    @field_validator("timestamp_utc")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        return v.astimezone(timezone.utc)

    @field_validator("observation")
    @classmethod
    def _neutral_language(cls, v: str) -> str:
        m = _FORBIDDEN.search(v)
        if m:
            raise ValueError(f"observation contains an unsupported legal or personal inference: '{m.group(0)}'")
        return v


class ReviewRequest(_Model):
    status: Literal["confirmed", "dismissed", "uncertain"]
    note: str = Field(default="", max_length=2000)


def event_json_schema() -> dict:
    return Event.model_json_schema()
