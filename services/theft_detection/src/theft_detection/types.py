"""Plain data containers shared by every pipeline stage.

Nothing here depends on a model, a tracker implementation or the web layer, so each
stage can be replaced without touching the others.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in analysed-frame pixels

PERSON = "person"
BAG = "bag"  # tracking category for every carried-object class (backpack, handbag, suitcase)


@dataclass(slots=True)
class Frame:
    image: np.ndarray | None  # BGR uint8; None for trace-only (no pixels) operation
    index: int                # index in the source stream
    ts: float                 # stream time in seconds (media time for files, monotonic for live)
    wall_ts: float            # time.time() when the frame was captured/decoded


@dataclass(slots=True)
class Detection:
    box: Box
    score: float
    label: str      # fine class from the detector, e.g. "backpack"
    category: str   # PERSON or BAG


@dataclass(slots=True)
class Track:
    """An ephemeral, camera-local track. IDs restart with every stream and carry no identity."""

    track_id: int
    category: str
    label: str
    box: Box
    score: float
    hits: int
    first_ts: float
    lost_s: float = 0.0  # 0.0 = matched to a detection on this update


@dataclass(slots=True)
class RawEvent:
    """Engine output before it is wrapped in the versioned public schema."""

    event_type: str
    severity: str
    onset_ts: float
    detected_ts: float
    score: float
    observation: str
    features: dict[str, float] = field(default_factory=dict)
    tracks: list[dict[str, str]] = field(default_factory=list)
    object_label: str | None = None
    object_ref: str | None = None
    uncertainties: list[str] = field(default_factory=list)
    zone_id: str | None = None
    dedup_key: str = ""


@dataclass(slots=True)
class Decision:
    """Transparent record of what happened to a stage-1 candidate (emitted or dismissed)."""

    ts: float
    kind: str
    outcome: str          # "emitted" | "dismissed"
    reason: str
    event_type: str | None = None
    features: dict[str, Any] = field(default_factory=dict)
