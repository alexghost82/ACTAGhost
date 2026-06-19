"""Integration Layer: connectors to external services and local resources.

A small connector framework with a registry. The MVP ships safe built-in
connectors (echo, HTTP fetch, sandboxed filesystem) and is structured so REST,
GraphQL, Webhook and MCP connectors can be added without touching the agents.
"""

from acta.integration.connectors import (
    CameraConnector,
    Connector,
    ConnectorRegistry,
    EchoConnector,
    FileSystemConnector,
    HttpConnector,
    default_registry,
)
from acta.integration.system import SystemConnector

__all__ = [
    "CameraConnector",
    "Connector",
    "ConnectorRegistry",
    "EchoConnector",
    "FileSystemConnector",
    "HttpConnector",
    "SystemConnector",
    "default_registry",
]
