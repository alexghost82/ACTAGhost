"""Camera / sensor registry and offline-safe frame capture.

The registry tracks :class:`CameraSpec` entries and produces :class:`VisionFrame`
objects on demand. Capture works with **no hardware**: a ``synthetic`` source
yields a deterministic frame (geometry + a reproducible scene seed), while a
file/URL source produces a frame that references the real asset so cloud/local
VLMs can analyze it. This keeps the whole subsystem runnable and testable
offline while remaining wired for real cameras (RTSP/HTTP/file) in production.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from acta.schemas import SensorType
from acta.vision.frames import CameraSpec, VisionFrame


class CameraRegistry:
    def __init__(self) -> None:
        self._cameras: dict[str, CameraSpec] = {}
        self._lock = threading.RLock()

    def register(self, spec: CameraSpec) -> CameraSpec:
        with self._lock:
            self._cameras[spec.id] = spec
        return spec

    def add(
        self,
        name: str,
        *,
        sensor_type: SensorType | str = SensorType.RGB,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        source: str = "synthetic",
        camera_id: str | None = None,
    ) -> CameraSpec:
        spec = CameraSpec(
            name=name,
            sensor_type=(
                sensor_type if isinstance(sensor_type, SensorType) else SensorType(sensor_type)
            ),
            width=width,
            height=height,
            fps=fps,
            source=source,
        )
        if camera_id:
            spec.id = camera_id
        return self.register(spec)

    def get(self, camera_id: str) -> CameraSpec | None:
        return self._cameras.get(camera_id)

    def list(self) -> list[CameraSpec]:
        return list(self._cameras.values())

    def remove(self, camera_id: str) -> bool:
        with self._lock:
            return self._cameras.pop(camera_id, None) is not None

    def set_enabled(self, camera_id: str, enabled: bool) -> bool:
        spec = self._cameras.get(camera_id)
        if spec is None:
            return False
        spec.enabled = enabled
        return True

    def capture(self, camera_id: str, *, sequence: int = 0) -> VisionFrame:
        spec = self._cameras.get(camera_id)
        if spec is None:
            raise KeyError(f"unknown camera '{camera_id}'")
        if not spec.enabled:
            raise RuntimeError(f"camera '{camera_id}' is disabled")
        timestamp = time.time()
        if spec.source not in ("synthetic", "", None):
            path = Path(spec.source)
            metadata = {"source_kind": "file" if path.exists() else "uri"}
            if path.exists():
                metadata["path"] = str(path)
            return VisionFrame(
                width=spec.width,
                height=spec.height,
                sensor_type=spec.sensor_type,
                camera_id=spec.id,
                timestamp=timestamp,
                source=spec.source,
                metadata=metadata,
            )
        # Synthetic, deterministic frame: reproducible scene seed per sequence.
        scene_seed = int(
            hashlib.sha256(f"{spec.id}|{sequence}".encode("utf-8")).hexdigest()[:8], 16
        )
        return VisionFrame(
            width=spec.width,
            height=spec.height,
            sensor_type=spec.sensor_type,
            camera_id=spec.id,
            timestamp=timestamp,
            source="synthetic",
            metadata={"source_kind": "synthetic", "scene_seed": scene_seed, "sequence": sequence},
        )
