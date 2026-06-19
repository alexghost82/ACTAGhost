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
        "ACTA_API_USERS",
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


def test_api_ready_returns_healthy_when_initialized(monkeypatch):
    client = _client_with_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock")
    resp = client.get("/api/ready")
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ready"
    assert body["checks"]["services"] is True
    assert body["checks"]["orchestrator"] is True
    assert body["checks"]["router"] is True
    assert body["checks"]["memory"] is True


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


def test_api_without_auth_config_uses_offline_default_admin(monkeypatch):
    client = _client_with_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock")
    chat = client.post("/api/chat", json={"text": "hello", "user_id": "alice"})
    assert chat.status_code == 200
    # Offline default admin can scope memory/audit to an arbitrary user.
    mem = client.get("/api/memory", params={"user_id": "alice"})
    assert mem.status_code == 200
    audit = client.get("/api/audit", params={"user_id": "alice"})
    assert audit.status_code == 200


def test_api_users_resolve_principal_and_unknown_is_401(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user,bob-token:bob:user,admin-token:owner:admin",
    )
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"X-API-Key": "alice-token"}).status_code == 200
    assert client.get("/api/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_non_admin_cannot_impersonate_or_read_other_users(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user,bob-token:bob:user",
    )
    as_alice = {"Authorization": "Bearer alice-token"}
    # No user_id in body -> request is scoped to principal user_id (alice).
    ok = client.post("/api/chat", json={"text": "hello"}, headers=as_alice)
    assert ok.status_code == 200
    forbidden_chat = client.post(
        "/api/chat",
        json={"text": "hello", "user_id": "bob"},
        headers=as_alice,
    )
    assert forbidden_chat.status_code == 403
    forbidden_mem = client.get("/api/memory", params={"user_id": "bob"}, headers=as_alice)
    assert forbidden_mem.status_code == 403
    forbidden_audit = client.get("/api/audit", params={"user_id": "bob"}, headers=as_alice)
    assert forbidden_audit.status_code == 403


def test_admin_can_scope_memory_and_audit_to_other_user(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user,admin-token:owner:admin",
    )
    as_alice = {"Authorization": "Bearer alice-token"}
    as_admin = {"Authorization": "Bearer admin-token"}
    assert client.post("/api/chat", json={"text": "hello"}, headers=as_alice).status_code == 200

    mem = client.get("/api/memory", params={"user_id": "alice"}, headers=as_admin)
    assert mem.status_code == 200
    assert isinstance(mem.json().get("records"), list)

    audit = client.get("/api/audit", params={"user_id": "alice"}, headers=as_admin)
    assert audit.status_code == 200
    entries = audit.json()["entries"]
    assert isinstance(entries, list)
    assert all(entry["details"].get("user_id") == "alice" for entry in entries)


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


def test_configured_token_rejects_missing_auth_for_chat_and_channels(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_AUTH_TOKEN="secret-token",
    )
    assert client.post("/api/chat", json={"text": "hello"}).status_code == 401
    assert client.get("/api/channels").status_code == 401


def test_user_token_cannot_impersonate_other_user_via_chat(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user,bob-token:bob:user",
    )
    as_alice = {"Authorization": "Bearer alice-token"}
    own = client.post("/api/chat", json={"text": "hello"}, headers=as_alice)
    assert own.status_code == 200
    other = client.post("/api/chat", json={"text": "hello", "user_id": "bob"}, headers=as_alice)
    assert other.status_code == 403


def test_user_token_cannot_read_other_user_memory_or_audit(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user,bob-token:bob:user",
    )
    as_alice = {"Authorization": "Bearer alice-token"}
    assert client.get("/api/memory", params={"user_id": "bob"}, headers=as_alice).status_code == 403
    assert client.get("/api/audit", params={"user_id": "bob"}, headers=as_alice).status_code == 403


def test_whatsapp_webhook_rejects_missing_or_bad_signature(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_AUTH_TOKEN="secret-token",
        ACTA_WHATSAPP_APP_SECRET="app-secret",
    )
    payload = {"entry": [{"changes": [{"value": {"messages": []}}]}]}
    raw = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer secret-token"}
    missing = client.post("/webhooks/whatsapp", content=raw, headers=headers)
    assert missing.status_code == 403
    bad = client.post(
        "/webhooks/whatsapp",
        content=raw,
        headers={**headers, "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert bad.status_code == 403


def test_memory_and_audit_pagination(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user",
    )
    as_alice = {"Authorization": "Bearer alice-token"}
    for idx in range(3):
        resp = client.post("/api/chat", json={"text": f"message {idx}"}, headers=as_alice)
        assert resp.status_code == 200

    mem = client.get("/api/memory", params={"limit": 2, "offset": 1}, headers=as_alice)
    assert mem.status_code == 200
    payload = mem.json()
    assert len(payload["records"]) <= 2
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert "pagination" in payload

    audit = client.get("/api/audit", params={"limit": 2, "offset": 1}, headers=as_alice)
    assert audit.status_code == 200
    audit_payload = audit.json()
    assert len(audit_payload["entries"]) <= 2
    assert audit_payload["limit"] == 2
    assert audit_payload["offset"] == 1
    assert "pagination" in audit_payload

    mem_v1 = client.get("/api/v1/memory", params={"limit": 1}, headers=as_alice)
    assert mem_v1.status_code == 200
    audit_v1 = client.get("/api/v1/audit", params={"limit": 1}, headers=as_alice)
    assert audit_v1.status_code == 200


def test_history_is_paginated_and_principal_scoped(monkeypatch):
    client = _client_with_env(
        monkeypatch,
        ACTA_DEFAULT_PROVIDER="mock",
        ACTA_API_USERS="alice-token:alice:user,bob-token:bob:user,admin-token:owner:admin",
    )
    as_alice = {"Authorization": "Bearer alice-token"}
    as_bob = {"Authorization": "Bearer bob-token"}
    as_admin = {"Authorization": "Bearer admin-token"}

    for text in ("first", "second", "third"):
        assert client.post("/api/chat", json={"text": text}, headers=as_alice).status_code == 200
    assert client.post("/api/chat", json={"text": "bob msg"}, headers=as_bob).status_code == 200

    own = client.get("/api/history", params={"limit": 2}, headers=as_alice)
    assert own.status_code == 200
    own_payload = own.json()
    assert len(own_payload["items"]) <= 2
    assert own_payload["limit"] == 2
    assert all("assistant_text" in item for item in own_payload["items"])

    forbidden = client.get("/api/history", params={"user_id": "bob"}, headers=as_alice)
    assert forbidden.status_code == 403

    admin_scoped = client.get("/api/history", params={"user_id": "bob", "limit": 5}, headers=as_admin)
    assert admin_scoped.status_code == 200
    items = admin_scoped.json()["items"]
    assert len(items) >= 1
    assert any("bob msg" in (item.get("user_text") or "") for item in items)

    assert client.get("/api/v1/history", params={"limit": 1}, headers=as_alice).status_code == 200
