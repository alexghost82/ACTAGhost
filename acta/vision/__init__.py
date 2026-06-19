"""AGENT Vision subsystem — advanced multimodal visual processing with VLMs.

Extends ACTA GHOST with Vision-Language Model support across many camera/sensor
families (RGB, infrared, thermal, depth, point cloud, multispectral). The design
mirrors the rest of ACTA: offline-first with deterministic fallbacks, encrypted
memory, capability-gated access and full auditing.
"""

from acta.vision.cameras import CameraRegistry
from acta.vision.frames import CameraSpec, VisionFrame
from acta.vision.lora import (
    LoRAAdapter,
    LoRAConfig,
    LoRARegistry,
    VisualInstructionTuner,
)
from acta.vision.pipeline import (
    EventBus,
    VisionAnalysis,
    VisionEvent,
    VisionPipeline,
    VisionService,
)
from acta.vision.preprocess import (
    PatchPlan,
    PreprocessConfig,
    pixel_shuffle,
    split_into_patches,
    visual_token_count,
)

__all__ = [
    "CameraRegistry",
    "CameraSpec",
    "VisionFrame",
    "PatchPlan",
    "PreprocessConfig",
    "pixel_shuffle",
    "split_into_patches",
    "visual_token_count",
    "LoRAAdapter",
    "LoRAConfig",
    "LoRARegistry",
    "VisualInstructionTuner",
    "EventBus",
    "VisionAnalysis",
    "VisionEvent",
    "VisionPipeline",
    "VisionService",
]
