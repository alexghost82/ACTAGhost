"""Append-only audit log of every agent action and decision."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from acta.config import Settings, get_settings


class AuditLog:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._path: Path = self.settings.ensure_data_dir() / "audit.log"
        self._lock = threading.Lock()
        self._buffer: list[dict[str, Any]] = []

    def record(self, actor: str, action: str, **details: Any) -> dict[str, Any]:
        entry = {
            "ts": time.time(),
            "actor": actor,
            "action": action,
            "details": details,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._buffer.append(entry)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return entry

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer[-n:])
