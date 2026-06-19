from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from acta.api.app import create_app
from acta.config import get_settings


def _client_with_env(monkeypatch, **env):
    keys = [
        "ACTA_API_AUTH_TOKEN",
        "ACTA_WHATSAPP_APP_SECRET",
        "ACTA_API_RATE_LIMIT_PER_MINUTE",
        "ACTA_API_MAX_BODY_SIZE_BYTES",
        "ACTA_DEFAULT_PROVIDER",
        "ACTA_ALLOW_SYSTEM_CONTROL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()
    return TestClient(create_app())


def test_api_health_is_public_without_token(monkeypatch):
    client = _client_with_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock")
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_api_protected_endpoints_require_token(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_AUTH_TOKEN="secret-token",
    )
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/memory").status_code == 401
    assert client.get("/api/audit").status_code == 401
    ok = client.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_whatsapp_webhook_signature_checked(monkeypatch):
    secret = "app-secret"
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_AUTH_TOKEN="secret-token",
        ACTA_WHATSAPP_APP_SECRET=secret,
    )
    payload = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
    raw = json.dumps(payload).encode("utf-8")
    bad = client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer secret-token",
            "X-Hub-Signature-256": "sha256=deadbeef",
        },
    )
    assert bad.status_code == 403

    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    good = client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer secret-token",
            "X-Hub-Signature-256": f"sha256={digest}",
        },
    )
    assert good.status_code == 200


def test_rate_limit_blocks_excess_requests(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_RATE_LIMIT_PER_MINUTE=1,
    )
    first = client.get("/api/health")
    second = client.get("/api/health")
    assert first.status_code == 200
    assert second.status_code == 429


def test_body_size_limit_blocks_large_payload(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_MAX_BODY_SIZE_BYTES=16,
    )
    resp = client.post(
        "/webhooks/telegram",
        content=b'{"message":"this payload is too big"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
