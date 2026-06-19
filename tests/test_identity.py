from __future__ import annotations

from acta.config import get_settings
from acta.identity import IdentityRegistry, Role


def test_identity_registry_defaults_to_offline_admin(monkeypatch):
    monkeypatch.delenv("ACTA_API_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ACTA_API_USERS", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    registry = IdentityRegistry.from_settings(settings)
    assert registry.auth_configured is False
    assert registry.default_session().principal.user_id == "default"
    assert registry.default_session().principal.role is Role.ADMIN


def test_identity_registry_parses_api_users_and_legacy_token(monkeypatch):
    monkeypatch.setenv("ACTA_API_USERS", "alice-token:alice:user")
    monkeypatch.setenv("ACTA_API_AUTH_TOKEN", "legacy-admin-token")
    get_settings.cache_clear()
    settings = get_settings()
    registry = IdentityRegistry.from_settings(settings)
    assert registry.auth_configured is True
    alice = registry.resolve("alice-token")
    assert alice is not None
    assert alice.principal.user_id == "alice"
    assert alice.principal.role is Role.USER
    legacy = registry.resolve("legacy-admin-token")
    assert legacy is not None
    assert legacy.principal.role is Role.ADMIN


def test_identity_registry_rejects_malformed_api_users(monkeypatch):
    monkeypatch.setenv("ACTA_API_USERS", "bad-entry")
    monkeypatch.delenv("ACTA_API_AUTH_TOKEN", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    try:
        IdentityRegistry.from_settings(settings)
    except ValueError as exc:
        assert "token:user_id:role" in str(exc)
    else:
        raise AssertionError("expected malformed ACTA_API_USERS to raise ValueError")
