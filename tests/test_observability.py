from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from acta.api.app import create_app
from acta.config import Settings, get_settings
from acta.security.audit import AuditLog


def _reset_env(monkeypatch, **env):
    keys = [
        "ACTA_API_AUTH_TOKEN",
        "ACTA_API_USERS",
        "ACTA_DEFAULT_PROVIDER",
        "ACTA_METRICS_ENABLED",
        "ACTA_LOG_JSON",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


def test_request_id_header_and_log_context(monkeypatch, caplog):
    _reset_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock")
    caplog.set_level(logging.INFO)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"X-Request-ID": "req-123"})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req-123"
    records = [r for r in caplog.records if r.name == "acta.api" and "http_request" in r.getMessage()]
    assert records
    assert any(getattr(record, "request_id", "") == "req-123" for record in records)


def test_audit_tail_reads_file_after_restart_and_buffer_bounded(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        default_provider="mock",
        audit_max_buffer=2,
        audit_log_max_bytes=200_000,
    )
    audit = AuditLog(settings)
    audit.record("a1", "event", user_id="u1")
    audit.record("a2", "event", user_id="u2")
    audit.record("a3", "event", user_id="u3")
    assert len(audit._buffer) == 2

    restarted = AuditLog(settings)
    tail = restarted.tail(5)
    assert [entry["actor"] for entry in tail][-2:] == ["a2", "a3"]


def test_audit_log_rotation(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        default_provider="mock",
        audit_max_buffer=10,
        audit_log_max_bytes=220,
    )
    audit = AuditLog(settings)
    for idx in range(10):
        audit.record("actor", "rotate", payload=("x" * 80), idx=idx)
    assert (tmp_path / "audit.log").exists()
    assert (tmp_path / "audit.log.1").exists()
    assert (tmp_path / "audit.log").stat().st_size <= settings.audit_log_max_bytes


def test_metrics_endpoint_flagged_off_returns_503(monkeypatch):
    _reset_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock", ACTA_METRICS_ENABLED="false")
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 503


def test_metrics_endpoint_graceful_without_dependency(monkeypatch):
    _reset_env(monkeypatch, ACTA_DEFAULT_PROVIDER="mock", ACTA_METRICS_ENABLED="true")
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code in {200, 503}
        if response.status_code == 200:
            assert "acta_http_requests_total" in response.text
        else:
            assert "prometheus disabled" in response.text
