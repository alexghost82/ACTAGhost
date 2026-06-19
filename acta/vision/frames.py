"""Domain models for the AGENT vision subsystem.

A :class:`VisionFrame` is the unit of visual data flowing through the pipeline.
Frames are intentionally lightweight: they carry shape metadata plus an optional
source reference (path / URL) or in-memory pixel payload. This lets the whole
pipeline run with **no heavy image dependencies** — pixel data is optional and
all preprocessing math operates on the frame geometry.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from acta.schemas import SensorType


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class CameraSpec:
    """Description of a camera / visual sensor known to the system."""

    id: str = field(default_factory=lambda: _uid("cam"))
    name: str = "camera"
    sensor_type: SensorType = SensorType.RGB
    width: int = 1280
    height: int = 720
    fps: int = 30
    source: str = "synthetic"  # "synthetic" | file path | rtsp/http URL
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.sensor_type, str):
            self.sensor_type = SensorType(self.sensor_type)
        if self.width <= 0 or self.height <= 0:
            raise ValueError("camera width/height must be positive")
        if self.fps <= 0:
            raise ValueError("camera fps must be positive")

    @property
    def channels(self) -> int:
        return self.sensor_type.channels

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "sensor_type": self.sensor_type.value,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "channels": self.channels,
            "source": self.source,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class VisionFrame:
    """A single captured frame plus its provenance."""

    width: int
    height: int
    sensor_type: SensorType = SensorType.RGB
    channels: int | None = None
    camera_id: str = "unknown"
    frame_id: str = field(default_factory=lambda: _uid("frame"))
    timestamp: float = field(default_factory=time.time)
    source: str | None = None
    pixels: list[float] | None = None  # optional flattened payload (synthetic)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.sensor_type, str):
            self.sensor_type = SensorType(self.sensor_type)
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame width/height must be positive")
        if self.channels is None:
            self.channels = self.sensor_type.channels

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    def to_dict(self, *, include_pixels: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "frame_id": self.frame_id,
            "camera_id": self.camera_id,
            "sensor_type": self.sensor_type.value,
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "timestamp": self.timestamp,
            "source": self.source,
            "metadata": self.metadata,
        }
        if include_pixels and self.pixels is not None:
            out["pixels"] = self.pixels
        return out
