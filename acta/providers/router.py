"""AI Router — picks a model provider per task with rule-based routing.

Routing rules can be configured manually; otherwise ACTA chooses automatically
based on the task profile (reasoning, coding, fast, multimodal). Any provider
that is unavailable transparently falls back to the offline mock provider so the
system never breaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from acta.config import Settings, get_settings
from acta.logging_config import get_logger
from acta.providers.base import ChatMessage, LLMProvider, LLMResponse
from acta.providers.mock import MockProvider

log = get_logger("router")


@dataclass
class RoutingRule:
    """Map a task profile to a preferred provider name."""

    profile: str
    provider: str


@dataclass
class CircuitState:
    failures: int = 0
    opened_until: float = 0.0


# Sensible default policy: prefer capable models for reasoning, fast models for
# routine work. Falls back automatically when a provider isn't configured.
DEFAULT_RULES: list[RoutingRule] = [
    RoutingRule(profile="reasoning", provider="anthropic"),
    RoutingRule(profile="planning", provider="anthropic"),
    RoutingRule(profile="coding", provider="openai"),
    RoutingRule(profile="fast", provider="gemini"),
    RoutingRule(profile="multimodal", provider="openai"),
    RoutingRule(profile="local", provider="ollama"),
    RoutingRule(profile="default", provider="openai"),
]


class AIRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._providers: dict[str, LLMProvider] = {}
        self._mock = MockProvider()
        self._circuits: dict[str, CircuitState] = {}
        self.rules: list[RoutingRule] = list(DEFAULT_RULES)
        self._build_providers()

    # -- provider registry ------------------------------------------------- #
    def _build_providers(self) -> None:
        s = self.settings
        self._providers["mock"] = self._mock
        if s.openai_api_key:
            from acta.providers.cloud import OpenAIProvider

            self._providers["openai"] = OpenAIProvider(s.openai_api_key, s.openai_model)
        if s.anthropic_api_key:
            from acta.providers.cloud import AnthropicProvider

            self._providers["anthropic"] = AnthropicProvider(s.anthropic_api_key, s.anthropic_model)
        if s.gemini_api_key:
            from acta.providers.cloud import GeminiProvider

            self._providers["gemini"] = GeminiProvider(s.gemini_api_key, s.gemini_model)
        # Ollama is registered lazily; availability is checked at routing time.
        from acta.providers.cloud import OllamaProvider

        self._providers["ollama"] = OllamaProvider(s.ollama_host, s.ollama_model)
        for name in self._providers:
            self._circuits.setdefault(name, CircuitState())

    def _is_circuit_open(self, provider_name: str) -> bool:
        if provider_name == "mock":
            return False
        state = self._circuits.setdefault(provider_name, CircuitState())
        return state.opened_until > time.monotonic()

    def _record_success(self, provider_name: str) -> None:
        state = self._circuits.setdefault(provider_name, CircuitState())
        state.failures = 0
        state.opened_until = 0.0

    def _record_failure(self, provider_name: str) -> None:
        if provider_name == "mock":
            return
        state = self._circuits.setdefault(provider_name, CircuitState())
        threshold = max(1, self.settings.provider_breaker_threshold)
        cooldown = max(0.0, self.settings.provider_breaker_cooldown)
        state.failures += 1
        if state.failures >= threshold:
            state.opened_until = time.monotonic() + cooldown
            log.warning(
                "Circuit opened for provider=%s failures=%d cooldown=%.1fs",
                provider_name,
                state.failures,
                cooldown,
            )

    def _select_provider_entry(self, profile: str = "default") -> tuple[str, LLMProvider]:
        """Return a candidate provider key + instance for this profile."""
        candidates = [r.provider for r in self.rules if r.profile == profile]
        candidates.append(self.settings.default_provider)
        candidates.extend(r.provider for r in self.rules if r.profile == "default")
        for name in candidates:
            prov = self._providers.get(name)
            if prov is None:
                continue
            if self._is_circuit_open(name):
                continue
            try:
                if prov.is_available():
                    return name, prov
            except Exception:
                continue
        log.debug("Routing profile=%s fell back to mock provider", profile)
        return "mock", self._mock

    # -- routing ----------------------------------------------------------- #
    def available_providers(self) -> list[str]:
        out = []
        for name, prov in self._providers.items():
            try:
                if prov.is_available():
                    out.append(name)
            except Exception:
                continue
        return out

    def real_available(self) -> bool:
        """True when at least one non-mock (real) provider is reachable."""
        return any(name != "mock" for name in self.available_providers())

    def select(self, profile: str = "default") -> LLMProvider:
        """Return the best available provider for the given task profile."""
        return self._select_provider_entry(profile)[1]

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        profile: str = "default",
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        provider_name, provider = self._select_provider_entry(profile)
        if provider_name == "mock":
            return self._mock.complete(messages, temperature=temperature, max_tokens=max_tokens)

        retries = max(0, self.settings.provider_max_retries)
        backoff_base = max(0.0, self.settings.provider_retry_backoff_base)
        attempts = retries + 1
        for attempt in range(attempts):
            try:
                result = provider.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._record_success(provider_name)
                return result
            except Exception as exc:  # pragma: no cover - network/runtime safety net
                self._record_failure(provider_name)
                is_last_attempt = attempt == attempts - 1
                if is_last_attempt:
                    break
                if backoff_base > 0.0:
                    time.sleep(backoff_base * (2**attempt))
                log.warning(
                    "Provider %s attempt %d/%d failed (%s), retrying",
                    provider_name,
                    attempt + 1,
                    attempts,
                    exc,
                )
        log.warning("Provider %s failed after retries; falling back to mock", provider_name)
        return self._mock.complete(messages, temperature=temperature, max_tokens=max_tokens)

    def describe_image(
        self,
        image_source: str | Path,
        *,
        mime_type: str | None = None,
        prompt: str = "",
        profile: str = "multimodal",
        max_tokens: int = 256,
    ) -> str | None:
        provider_name, provider = self._select_provider_entry(profile)
        if provider_name == "mock":
            return None
        try:
            result = provider.describe_image(
                image_source,
                mime_type=mime_type,
                prompt=prompt,
                max_tokens=max_tokens,
            )
            if result:
                self._record_success(provider_name)
                return result
            self._record_failure(provider_name)
            return None
        except Exception:  # pragma: no cover - network/runtime safety net
            self._record_failure(provider_name)
            return None
