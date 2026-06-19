from __future__ import annotations

from fastapi.testclient import TestClient

from acta.api.app import create_app
from acta.config import get_settings


def _client(monkeypatch, **env):
    for key in (
        "ACTA_API_AUTH_TOKEN",
        "ACTA_API_USERS",
        "ACTA_DEFAULT_PROVIDER",
        "ACTA_VISION_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ACTA_DEFAULT_PROVIDER", "mock")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    get_settings.cache_clear()
    return TestClient(create_app())


def test_vision_status_and_camera_crud(monkeypatch):
    with _client(monkeypatch, ACTA_VISION_ENABLED="true") as client:
        status = client.get("/api/vision/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is True

        created = client.post(
            "/api/cameras",
            json={"name": "lobby", "sensor_type": "thermal", "width": 640, "height": 480},
        )
        assert created.status_code == 200
        cam_id = created.json()["camera"]["id"]

        listed = client.get("/api/cameras")
        assert listed.status_code == 200
        assert any(c["id"] == cam_id for c in listed.json()["cameras"])

        analyzed = client.post(
            "/api/vision/analyze", json={"camera_id": cam_id, "instruction": "scan"}
        )
        assert analyzed.status_code == 200
        body = analyzed.json()
        assert body["ok"] is True
        assert body["analysis"]["visual_tokens"] > 0

        deleted = client.delete(f"/api/cameras/{cam_id}")
        assert deleted.status_code == 200
        assert client.delete(f"/api/cameras/{cam_id}").status_code == 404


def test_vision_analyze_inline_frame(monkeypatch):
    with _client(monkeypatch, ACTA_VISION_ENABLED="true") as client:
        r = client.post(
            "/api/vision/analyze", json={"width": 320, "height": 240, "sensor_type": "depth"}
        )
        assert r.status_code == 200
        assert r.json()["analysis"]["analysis"]["provider"] == "mock-vlm"


def test_vision_analyze_requires_target(monkeypatch):
    with _client(monkeypatch, ACTA_VISION_ENABLED="true") as client:
        assert client.post("/api/vision/analyze", json={}).status_code == 422


def test_vision_analyze_camera_not_found(monkeypatch):
    with _client(monkeypatch, ACTA_VISION_ENABLED="true") as client:
        assert client.post("/api/vision/analyze", json={"camera_id": "ghost"}).status_code == 404


def test_vision_disabled_returns_409(monkeypatch):
    with _client(monkeypatch, ACTA_VISION_ENABLED="false") as client:
        r = client.post("/api/vision/analyze", json={"width": 100, "height": 100})
        assert r.status_code == 409


def test_register_camera_invalid(monkeypatch):
    with _client(monkeypatch, ACTA_VISION_ENABLED="true") as client:
        r = client.post("/api/cameras", json={"name": "bad", "sensor_type": "not-a-sensor"})
        assert r.status_code == 422
