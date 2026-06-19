"""Connector framework for external integrations."""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any

from acta.config import Settings, get_settings


class Connector(abc.ABC):
    name: str = "connector"

    @abc.abstractmethod
    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        ...


class EchoConnector(Connector):
    """Always-available connector used for testing and dry runs."""

    name = "echo"

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"connector": self.name, "action": action, "params": params, "ok": True}


class HttpConnector(Connector):
    """Minimal HTTP connector (GET/POST) for REST/webhook style integrations."""

    name = "http"

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        method = (params.get("method") or action or "GET").upper()
        url = params.get("url")
        if not url:
            return {"ok": False, "error": "missing 'url'"}
        try:
            resp = httpx.request(
                method,
                url,
                params=params.get("query"),
                json=params.get("json"),
                headers=params.get("headers"),
                timeout=params.get("timeout", 15),
            )
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:2000]
            return {"ok": resp.is_success, "status": resp.status_code, "body": body}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class FileSystemConnector(Connector):
    """Sandboxed filesystem access confined to the ACTA data directory."""

    name = "fs"

    def __init__(self, root: Path | None = None, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.root = (root or (s.ensure_data_dir() / "workspace")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, rel: str) -> Path:
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root)):
            raise PermissionError("path escapes sandbox")
        return target

    def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if action == "write":
                path = self._safe(params["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(params.get("content", ""), encoding="utf-8")
                return {"ok": True, "path": str(path)}
            if action == "read":
                path = self._safe(params["path"])
                return {"ok": True, "content": path.read_text(encoding="utf-8")}
            if action == "list":
                return {"ok": True, "entries": [p.name for p in self.root.iterdir()]}
            return {"ok": False, "error": f"unknown action '{action}'"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def names(self) -> list[str]:
        return list(self._connectors)

    def execute(self, connector: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        conn = self.get(connector)
        if conn is None:
            return {"ok": False, "error": f"unknown connector '{connector}'"}
        return conn.execute(action, params)


def default_registry(settings: Settings | None = None) -> ConnectorRegistry:
    from acta.integration.system import SystemConnector

    reg = ConnectorRegistry()
    reg.register(EchoConnector())
    reg.register(HttpConnector())
    reg.register(FileSystemConnector(settings=settings))
    reg.register(SystemConnector(settings=settings))
    return reg
