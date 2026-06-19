"""Vision-Language Model (VLM) providers for the AGENT subsystem.

A VLM is exposed as a small, swappable service behind :class:`VLMProvider`:

* :class:`MockVLMProvider` — deterministic, offline, always-available. Produces a
  structured, reproducible analysis from frame geometry + instruction so the
  whole pipeline runs with no credentials or hardware.
* :class:`CloudVLMProvider` — delegates to the existing AI Router's vision hook
  (OpenAI / Gemini) when a frame references a real image file/URL.
* :class:`LocalVLMProvider` — a quantized local model via the Ollama HTTP API
  (e.g. ``llava``), honoring a :class:`QuantizationConfig` for low-resource runs.

:func:`build_vlm_provider` wires these together per configuration with graceful
fallback (``local → cloud → mock``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from acta.logging_config import get_logger

if TYPE_CHECKING:  # avoid import cycles at runtime
    from acta.config import Settings
    from acta.providers.router import AIRouter
    from acta.vision.frames import VisionFrame

log = get_logger("vlm")

_QUANT_BITS = {"none": 32, "fp16": 16, "int8": 8, "int4": 4}


@dataclass(slots=True)
class QuantizationConfig:
    """Quantization settings for local VLM execution."""

    mode: str = "int4"

    def __post_init__(self) -> None:
        self.mode = (self.mode or "none").lower()
        if self.mode not in _QUANT_BITS:
            raise ValueError(f"unsupported quantization mode: {self.mode}")

    @property
    def bits(self) -> int:
        return _QUANT_BITS[self.mode]

    @property
    def memory_factor(self) -> float:
        """Approx. weight-memory multiplier vs. fp32 (1.0)."""
        return self.bits / 32.0


@dataclass(slots=True)
class VLMRequest:
    frame: "VisionFrame"
    instruction: str = "Analyze this frame."
    max_tokens: int = 256
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VLMResult:
    text: str
    provider: str
    model: str
    objects: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "provider": self.provider,
            "model": self.model,
            "objects": self.objects,
            "confidence": round(self.confidence, 4),
        }


class VLMProvider:
    name: str = "base"
    model: str = "base"

    def is_available(self) -> bool:
        return True

    def analyze(self, request: VLMRequest) -> VLMResult | None:  # pragma: no cover - abstract
        raise NotImplementedError


# Vocabulary the offline mock draws from per sensor family — keeps annotations
# plausible and deterministic without any model.
_SENSOR_VOCAB: dict[str, list[str]] = {
    "rgb": ["person", "vehicle", "door", "monitor", "plant", "package"],
    "grayscale": ["edge", "contour", "motion-blob", "silhouette"],
    "infrared": ["heat-signature", "warm-body", "engine", "leak"],
    "thermal": ["hotspot", "person", "machinery", "thermal-gradient"],
    "depth": ["near-surface", "far-wall", "obstacle", "gap"],
    "pointcloud": ["plane", "cluster", "edge-point", "surface-normal"],
    "multispectral": ["vegetation-index", "moisture", "material-a", "material-b"],
}


class MockVLMProvider(VLMProvider):
    """Deterministic offline VLM. Reproducible analysis from frame + prompt."""

    name = "mock-vlm"
    model = "acta-vlm-offline"

    def is_available(self) -> bool:
        return True

    def analyze(self, request: VLMRequest) -> VLMResult:
        frame = request.frame
        sensor = frame.sensor_type.value
        seed = int(
            hashlib.sha256(
                f"{frame.frame_id}|{frame.camera_id}|{request.instruction}".encode("utf-8")
            ).hexdigest(),
            16,
        )
        vocab = _SENSOR_VOCAB.get(sensor, _SENSOR_VOCAB["rgb"])
        n = 2 + (seed % 3)
        raw_objects = [vocab[(seed >> (i * 3)) % len(vocab)] for i in range(n)]
        seen: set[str] = set()
        objects: list[str] = []
        for obj in raw_objects:
            if obj not in seen:
                seen.add(obj)
                objects.append(obj)
        confidence = 0.55 + (seed % 40) / 100.0
        text = (
            f"[{sensor}] {frame.width}x{frame.height} frame from '{frame.camera_id}': "
            f"detected {', '.join(objects)}. "
            f"Instruction: {request.instruction.strip()}"
        )
        return VLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            objects=objects,
            confidence=round(confidence, 3),
            raw={"offline": True, "seed": seed % 1_000_000},
        )


class CloudVLMProvider(VLMProvider):
    """Router-backed cloud vision (OpenAI/Gemini) for frames with real images."""

    name = "cloud-vlm"

    def __init__(self, router: "AIRouter", *, profile: str = "multimodal") -> None:
        self._router = router
        self._profile = profile
        self.model = "router"

    def is_available(self) -> bool:
        try:
            return self._router.real_available()
        except Exception:  # pragma: no cover - defensive
            return False

    def analyze(self, request: VLMRequest) -> VLMResult | None:
        frame = request.frame
        source = frame.source or frame.metadata.get("path") or frame.metadata.get("url")
        if not source:
            return None
        description = self._router.describe_image(
            str(source),
            mime_type=frame.metadata.get("mime_type"),
            prompt=request.instruction,
            profile=self._profile,
            max_tokens=request.max_tokens,
        )
        if not description:
            return None
        return VLMResult(
            text=description,
            provider=self.name,
            model=f"router:{self._profile}",
            objects=[],
            confidence=0.0,
            raw={"source": str(source)},
        )


class LocalVLMProvider(VLMProvider):
    """Quantized local VLM via the Ollama HTTP API (e.g. ``llava``)."""

    name = "local-vlm"

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llava",
        *,
        quantization: QuantizationConfig | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.quantization = quantization or QuantizationConfig()

    def is_available(self) -> bool:
        import httpx

        try:
            httpx.get(f"{self.host}/api/tags", timeout=1.5)
            return True
        except Exception:
            return False

    def _image_b64(self, frame: "VisionFrame") -> str | None:
        import base64
        from pathlib import Path

        source = frame.source or frame.metadata.get("path")
        if not source:
            return None
        path = Path(str(source))
        if not path.exists():
            return None
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def analyze(self, request: VLMRequest) -> VLMResult | None:
        import httpx

        image_b64 = self._image_b64(request.frame)
        if image_b64 is None:
            return None
        payload = {
            "model": self.model,
            "prompt": request.instruction,
            "images": [image_b64],
            "stream": False,
            "options": {
                "num_predict": request.max_tokens,
                "num_ctx": 4096,
                "quantization": self.quantization.mode,
            },
        }
        try:
            resp = httpx.post(f"{self.host}/api/generate", json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except Exception:  # pragma: no cover - network safety net
            return None
        text = (data.get("response") or "").strip()
        if not text:
            return None
        return VLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            objects=[],
            confidence=0.0,
            raw={"quantization": self.quantization.mode},
        )


class FallbackVLMProvider(VLMProvider):
    """Try providers in order; return the first non-empty analysis."""

    name = "fallback-vlm"

    def __init__(self, providers: list[VLMProvider]) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers
        self.model = "+".join(p.name for p in providers)

    def is_available(self) -> bool:
        return any(self._safe_available(p) for p in self._providers)

    @staticmethod
    def _safe_available(provider: VLMProvider) -> bool:
        try:
            return provider.is_available()
        except Exception:  # pragma: no cover - defensive
            return False

    def analyze(self, request: VLMRequest) -> VLMResult | None:
        for provider in self._providers:
            if not self._safe_available(provider):
                continue
            try:
                result = provider.analyze(request)
            except Exception:  # pragma: no cover - defensive
                log.debug("VLM provider %s raised; trying next", provider.name, exc_info=True)
                continue
            if result is not None:
                return result
        return None


def build_vlm_provider(
    settings: "Settings",
    router: "AIRouter | None" = None,
) -> VLMProvider:
    """Construct the configured VLM provider with graceful fallback."""
    strategy = (settings.vlm_provider or "auto").lower()
    quant = QuantizationConfig(settings.vlm_quantization)
    local = LocalVLMProvider(
        settings.vlm_local_host, settings.vlm_local_model, quantization=quant
    )
    mock = MockVLMProvider()

    if strategy == "mock":
        return mock
    if strategy == "local":
        return FallbackVLMProvider([local, mock])
    if strategy == "cloud":
        if router is not None:
            return FallbackVLMProvider(
                [CloudVLMProvider(router, profile=settings.vlm_cloud_profile), mock]
            )
        return mock
    # "auto": local → cloud → mock
    chain: list[VLMProvider] = [local]
    if router is not None:
        chain.append(CloudVLMProvider(router, profile=settings.vlm_cloud_profile))
    chain.append(mock)
    return FallbackVLMProvider(chain)
