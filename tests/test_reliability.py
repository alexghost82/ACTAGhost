from __future__ import annotations

import time
import warnings

from fastapi.testclient import TestClient

from acta.api.app import create_app
from acta.channels import TelegramChannel, WhatsAppChannel
from acta.channels.base import ChannelHub, IncomingMessage
from acta.config import get_settings
from acta.providers.base import ChatMessage, LLMProvider, LLMResponse


class _FlakyProvider(LLMProvider):
    name = "flaky"

    def __init__(self, failures_before_success: int) -> None:
        super().__init__("flaky-test")
        self.failures_before_success = failures_before_success
        self.calls = 0

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError("temporary failure")
        return LLMResponse(text="ok", provider=self.name, model=self.model)


class _AlwaysFailProvider(LLMProvider):
    name = "always-fail"

    def __init__(self) -> None:
        super().__init__("always-fail-test")
        self.calls = 0

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        self.calls += 1
        raise RuntimeError("boom")


class _DummyHub(ChannelHub):
    def __init__(self, orchestrator) -> None:
        super().__init__(orchestrator)
        self.calls = 0

    def handle(self, msg: IncomingMessage) -> str:
        self.calls += 1
        return "ack"


def test_router_retries_transient_provider_errors(services):
    router = services.router
    flaky = _FlakyProvider(failures_before_success=1)
    router._providers["flaky"] = flaky
    router.settings.default_provider = "flaky"
    router.settings.provider_max_retries = 2
    router.settings.provider_retry_backoff_base = 0.0
    router.settings.provider_breaker_threshold = 3
    response = router.complete([ChatMessage(role="user", content="hello")], profile="default")
    assert response.text == "ok"
    assert flaky.calls == 2


def test_router_opens_circuit_and_falls_back_to_mock(services):
    router = services.router
    broken = _AlwaysFailProvider()
    router._providers["broken"] = broken
    router.settings.default_provider = "broken"
    router.settings.provider_max_retries = 0
    router.settings.provider_retry_backoff_base = 0.0
    router.settings.provider_breaker_threshold = 1
    router.settings.provider_breaker_cooldown = 60.0

    first = router.complete([ChatMessage(role="user", content="one")], profile="default")
    second = router.complete([ChatMessage(role="user", content="two")], profile="default")
    assert first.provider == "mock"
    assert second.provider == "mock"
    assert broken.calls == 1
    assert router.select("default").name == "mock"


def test_telegram_update_id_is_idempotent(orchestrator, services, monkeypatch):
    hub = _DummyHub(orchestrator)
    channel = TelegramChannel(hub, services.settings)
    sent: list[str] = []
    
    def _send_tg(chat_id, text):
        sent.append(f"{chat_id}:{text}")
        return {"ok": True}

    monkeypatch.setattr(channel, "send_message", _send_tg)
    update = {
        "update_id": 42,
        "message": {"chat": {"id": 123}, "text": "hello", "from": {"username": "u"}},
    }
    channel.handle_update(update)
    channel.handle_update(update)
    assert hub.calls == 1
    assert len(sent) == 1


def test_whatsapp_message_id_is_idempotent(orchestrator, services, monkeypatch):
    hub = _DummyHub(orchestrator)
    channel = WhatsAppChannel(hub, services.settings)
    sent: list[str] = []

    def _send_wa(to, text):
        sent.append(f"{to}:{text}")
        return {"ok": True}

    monkeypatch.setattr(channel, "send_message", _send_wa)
    payload = {
        "entry": [
            {"changes": [{"value": {"messages": [
                {"id": "wamid-1", "type": "text", "from": "972500000000", "text": {"body": "hi"}}
            ]}}]}
        ]
    }
    assert channel.handle_webhook(payload) == 1
    assert channel.handle_webhook(payload) == 0
    assert hub.calls == 1
    assert len(sent) == 1


def test_app_lifespan_starts_and_stops_telegram_poller(monkeypatch):
    monkeypatch.setenv("ACTA_DEFAULT_PROVIDER", "mock")
    monkeypatch.setenv("ACTA_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.delenv("ACTA_TELEGRAM_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()

    def fake_poll_forever(self, interval: float = 1.0) -> None:
        self._running = True
        while self._running:
            time.sleep(0.01)

    monkeypatch.setattr(TelegramChannel, "poll_forever", fake_poll_forever)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            assert app.state.telegram._poller_thread is not None
            assert app.state.telegram._poller_thread.is_alive()
        assert app.state.telegram._poller_thread is not None
        assert not app.state.telegram._poller_thread.is_alive()
        assert not any(
            issubclass(w.category, DeprecationWarning) and "on_event" in str(w.message)
            for w in caught
        )
