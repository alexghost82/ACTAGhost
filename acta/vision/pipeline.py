"""Event-driven vision pipeline orchestrating the AGENT visual workflow.

Flow per frame::

    capture → preprocess (patch split + pixel-shuffle token budget)
            → VLM analyze → (optional LoRA-tuned projection)
            → persist annotation to encrypted memory → audit

A tiny synchronous :class:`EventBus` makes the workflow observable and lets
other components react to ``frame_received`` / ``frame_preprocessed`` /
``frame_analyzed`` events (the event-driven requirement) without coupling.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from acta.logging_config import get_logger
from acta.providers.vlm import VLMProvider, VLMRequest, VLMResult, build_vlm_provider
from acta.schemas import MemoryKind
from acta.vision.cameras import CameraRegistry
from acta.vision.frames import VisionFrame
from acta.vision.lora import LoRARegistry
from acta.vision.preprocess import (
    PatchPlan,
    PreprocessConfig,
    split_into_patches,
    visual_token_count,
)

if TYPE_CHECKING:
    from acta.config import Settings
    from acta.providers.router import AIRouter

log = get_logger("vision.pipeline")


@dataclass(slots=True)
class VisionEvent:
    type: str
    frame_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Minimal synchronous publish/subscribe bus."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[VisionEvent], None]]] = defaultdict(list)
        self.history: list[VisionEvent] = []

    def subscribe(self, event_type: str, handler: Callable[[VisionEvent], None]) -> None:
        self._subs[event_type].append(handler)

    def publish(self, event: VisionEvent) -> None:
        self.history.append(event)
        for handler in self._subs.get(event.type, []):
            try:
                handler(event)
            except Exception:  # pragma: no cover - subscriber must not break flow
                log.debug("event subscriber failed for %s", event.type, exc_info=True)


@dataclass(slots=True)
class VisionAnalysis:
    frame: VisionFrame
    plan: PatchPlan
    result: VLMResult
    visual_tokens: int
    lora_adapter: str | None = None
    persisted_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame.to_dict(),
            "patch_plan": self.plan.to_dict(),
            "analysis": self.result.to_dict(),
            "visual_tokens": self.visual_tokens,
            "lora_adapter": self.lora_adapter,
            "memory_id": self.persisted_id,
        }


class VisionPipeline:
    def __init__(
        self,
        *,
        vlm_provider: VLMProvider,
        config: PreprocessConfig,
        memory: Any | None = None,
        audit: Any | None = None,
        permissions: Any | None = None,
        lora_registry: LoRARegistry | None = None,
        event_bus: EventBus | None = None,
        default_instruction: str = "Analyze this frame.",
        max_tokens: int = 256,
    ) -> None:
        self.vlm = vlm_provider
        self.config = config
        self.memory = memory
        self.audit = audit
        self.permissions = permissions
        self.lora = lora_registry
        self.bus = event_bus or EventBus()
        self.default_instruction = default_instruction
        self.max_tokens = max_tokens

    def analyze_frame(
        self,
        frame: VisionFrame,
        instruction: str | None = None,
        *,
        user_id: str = "default",
        agent: str = "multimodal",
        persist: bool = True,
    ) -> VisionAnalysis:
        if self.permissions is not None:
            self.permissions.require(agent, "vision.process")
        instruction = instruction or self.default_instruction

        self.bus.publish(VisionEvent("frame_received", frame.frame_id, {"camera": frame.camera_id}))

        plan = split_into_patches(frame.width, frame.height, self.config)
        tokens = visual_token_count(plan, self.config)
        self.bus.publish(
            VisionEvent(
                "frame_preprocessed",
                frame.frame_id,
                {"tiles": plan.tile_count, "visual_tokens": tokens},
            )
        )

        request = VLMRequest(frame=frame, instruction=instruction, max_tokens=self.max_tokens)
        result = self.vlm.analyze(request)
        if result is None:
            result = VLMResult(
                text=f"[{frame.sensor_type.value}] frame {frame.frame_id} (no analysis available)",
                provider="none",
                model="none",
            )

        adapter = self.lora.active() if self.lora is not None else None
        adapter_name = adapter.config.name if adapter is not None else None

        self.bus.publish(
            VisionEvent(
                "frame_analyzed",
                frame.frame_id,
                {"provider": result.provider, "objects": result.objects},
            )
        )

        persisted_id: str | None = None
        if persist and self.memory is not None:
            record = self.memory.add(
                MemoryKind.VISUAL,
                result.text,
                tags=["vision", frame.sensor_type.value, *result.objects],
                metadata={
                    "frame_id": frame.frame_id,
                    "camera_id": frame.camera_id,
                    "sensor_type": frame.sensor_type.value,
                    "provider": result.provider,
                    "confidence": result.confidence,
                    "visual_tokens": tokens,
                    "tiles": plan.tile_count,
                },
                user_id=user_id,
            )
            persisted_id = record.id

        if self.audit is not None:
            self.audit.record(
                "vision",
                "frame_analyzed",
                frame_id=frame.frame_id,
                camera_id=frame.camera_id,
                provider=result.provider,
                user_id=user_id,
            )

        return VisionAnalysis(
            frame=frame,
            plan=plan,
            result=result,
            visual_tokens=tokens,
            lora_adapter=adapter_name,
            persisted_id=persisted_id,
        )


class VisionService:
    """Facade bundling the registry, VLM provider, LoRA and pipeline.

    Built once and shared (mirrors :class:`acta.agents.base.AgentServices`).
    """

    def __init__(
        self,
        *,
        settings: "Settings",
        registry: CameraRegistry,
        pipeline: VisionPipeline,
        lora: LoRARegistry,
        enabled: bool,
    ) -> None:
        self.settings = settings
        self.cameras = registry
        self.pipeline = pipeline
        self.lora = lora
        self.enabled = enabled

    @property
    def bus(self) -> EventBus:
        return self.pipeline.bus

    @classmethod
    def build(
        cls,
        settings: "Settings",
        *,
        router: "AIRouter | None" = None,
        memory: Any | None = None,
        audit: Any | None = None,
        permissions: Any | None = None,
    ) -> "VisionService":
        config = PreprocessConfig(
            patch_size=settings.vision_patch_size,
            min_patches=settings.vision_min_patches,
            max_patches=settings.vision_max_patches,
            pixel_shuffle_scale=settings.vision_pixel_shuffle_scale,
            hidden_size=settings.vision_hidden_size,
        )
        provider = build_vlm_provider(settings, router)
        lora = LoRARegistry(store_dir=settings.data_dir / "vision" / "lora")
        pipeline = VisionPipeline(
            vlm_provider=provider,
            config=config,
            memory=memory,
            audit=audit,
            permissions=permissions,
            lora_registry=lora,
            default_instruction=settings.vlm_instruction,
            max_tokens=settings.vlm_max_tokens,
        )
        registry = CameraRegistry()
        return cls(
            settings=settings,
            registry=registry,
            pipeline=pipeline,
            lora=lora,
            enabled=settings.vision_enabled,
        )

    def capture_and_analyze(
        self,
        camera_id: str,
        instruction: str | None = None,
        *,
        user_id: str = "default",
        agent: str = "multimodal",
        persist: bool = True,
        sequence: int = 0,
    ) -> VisionAnalysis:
        frame = self.cameras.capture(camera_id, sequence=sequence)
        return self.pipeline.analyze_frame(
            frame, instruction, user_id=user_id, agent=agent, persist=persist
        )
