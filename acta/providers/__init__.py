"""Unified Provider Layer + AI Router.

A single abstraction (:class:`LLMProvider`) wraps local and cloud models so the
rest of ACTA never has to care which model answered. :class:`AIRouter` selects a
provider per task with rule-based routing and graceful fallback to the offline
mock provider.
"""

from acta.providers.base import ChatMessage, LLMProvider, LLMResponse
from acta.providers.mock import MockProvider
from acta.providers.router import AIRouter

__all__ = ["ChatMessage", "LLMProvider", "LLMResponse", "MockProvider", "AIRouter"]
