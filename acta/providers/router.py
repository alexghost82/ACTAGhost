"""AI Router — picks a model provider per task with rule-based routing.

Routing rules can be configured manually; otherwise ACTA chooses automatically
based on the task profile (reasoning, coding, fast, multimodal). Any provider
that is unavailable transparently falls back to the offline mock provider so the
system never breaks.
"""

from __future__ import annotations

from dataclasses import dataclass

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


# Sensible default policy: prefer capable models for reasoning, fast models for
# routine work. Falls back automatically when a provider isn't configured.
DEFAULT_RULES: list[RoutingRule] = [
    RoutingRule(profile="reasoning", provider="anthropic"),
    RoutingRule(profile="planning", provider="anthropic"),
    RoutingRule(profile="coding", provider="openai"),
    RoutingRule(profile="fast", provider="gemini"),
    RoutingRule(profile="local", provider="ollama"),
    RoutingRule(profile="default", provider="openai"),
]


class AIRouter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._providers: dict[str, LLMProvider] = {}
        self._mock = MockProvider()
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
        # 1) explicit rule for the profile
        candidates = [r.provider for r in self.rules if r.profile == profile]
        # 2) configured global default
        candidates.append(self.settings.default_provider)
        # 3) the generic default rule
        candidates.extend(r.provider for r in self.rules if r.profile == "default")
        for name in candidates:
            prov = self._providers.get(name)
            if prov is None:
                continue
            try:
                if prov.is_available():
                    return prov
            except Exception:
                continue
        log.debug("Routing profile=%s fell back to mock provider", profile)
        return self._mock

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        profile: str = "default",
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        provider = self.select(profile)
        try:
            return provider.complete(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        except Exception as exc:  # pragma: no cover - network/runtime safety net
            log.warning("Provider %s failed (%s); falling back to mock", provider.name, exc)
            return self._mock.complete(messages, temperature=temperature, max_tokens=max_tokens)
