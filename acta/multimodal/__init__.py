"""Multimodal Layer: normalize varied inputs to text and render outputs.

Text is fully handled in the MVP. Voice (Whisper STT / Piper TTS) and image/video
inputs expose clean interfaces with graceful fallbacks so they can be wired to
real models without changing the agent pipeline.
"""

from acta.multimodal.processor import MultimodalProcessor

__all__ = ["MultimodalProcessor"]
