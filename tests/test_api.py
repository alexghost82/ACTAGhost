from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from acta.api.app import create_app
from acta.config import get_settings


def _configure_env(monkeypatch, **env: str) -> None:
    keys = [
        "ACTA_API_AUTH_TOKEN",
        "ACTA_API_USERS",
        "ACTA_DEFAULT_PROVIDER",
        "ACTA_ALLOW_SYSTEM_CONTROL",
        "ACTA_WHATSAPP_VERIFY_TOKEN",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


def test_api_endpoints_offline_default_admin_with_lifespan(monkeypatch):
    _configure_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock", ACTA_WHATSAPP_VERIFY_TOKEN="verify-token")
    with TestClient(create_app()) as client:
        health = client.get("/api/health")
        assert health.status_code == 200

        ready = client.get("/api/ready")
        if ready.status_code != 404:
            assert ready.status_code == 200

        status = client.get("/api/status")
        assert status.status_code == 200

        agents = client.get("/api/agents")
        assert agents.status_code == 200
        assert agents.json()["count"] >= 1

        chat = client.post("/api/chat", json={"text": "hello from offline test", "user_id": "alice"})
        assert chat.status_code == 200
        assert chat.json()["answer"]

        memory = client.get("/api/memory", params={"user_id": "alice"})
        assert memory.status_code == 200
        assert isinstance(memory.json()["records"], list)

        audit = client.get("/api/audit", params={"user_id": "alice"})
        assert audit.status_code == 200
        assert isinstance(audit.json()["entries"], list)

        channels = client.get("/api/channels")
        assert channels.status_code == 200
        assert "telegram" in channels.json() and "whatsapp" in channels.json()

        telegram_webhook = client.post("/webhooks/telegram", json={"update_id": 1})
        assert telegram_webhook.status_code == 200

        verify = client.get(
            "/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-token",
                "hub.challenge": "challenge-value",
            },
        )
        assert verify.status_code == 200
        assert verify.text == "challenge-value"

        whatsapp_payload = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
        whatsapp_webhook = client.post("/webhooks/whatsapp", content=json.dumps(whatsapp_payload))
        assert whatsapp_webhook.status_code == 200


def test_api_token_mode_requires_auth_and_accepts_valid_token(monkeypatch):
    _configure_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_AUTH_TOKEN="top-secret-token",
        ACTA_WHATSAPP_VERIFY_TOKEN="verify-token",
    )
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/agents").status_code == 401
        assert client.post("/api/chat", json={"text": "hello"}).status_code == 401
        assert client.get("/api/memory").status_code == 401
        assert client.get("/api/audit").status_code == 401
        assert client.get("/api/channels").status_code == 401
        assert client.post("/webhooks/telegram", json={"update_id": 2}).status_code == 401
        assert client.post("/webhooks/whatsapp", json={"entry": []}).status_code == 401

        auth = {"Authorization": "Bearer top-secret-token"}
        assert client.get("/api/status", headers=auth).status_code == 200
        assert client.get("/api/agents", headers=auth).status_code == 200
        assert client.post("/api/chat", json={"text": "hello with token"}, headers=auth).status_code == 200
        assert client.get("/api/memory", headers=auth).status_code == 200
        assert client.get("/api/audit", headers=auth).status_code == 200
        assert client.get("/api/channels", headers=auth).status_code == 200
        assert client.post("/webhooks/telegram", json={"update_id": 3}, headers=auth).status_code == 200
        assert client.post("/webhooks/whatsapp", json={"entry": []}, headers=auth).status_code == 200


def test_api_ready_endpoint_is_optional(monkeypatch):
    _configure_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock")
    with TestClient(create_app()) as client:
        response = client.get("/api/ready")
    assert response.status_code in {200, 404}
    if response.status_code == 200:
        assert isinstance(response.json(), dict)
    else:
        pytest.skip("`/api/ready` is not implemented in this branch")
