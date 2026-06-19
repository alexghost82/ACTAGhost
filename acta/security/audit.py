"""Append-only audit log of every agent action and decision."""

from __future__ import annotations

from collections import deque
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
        self._rotated_path: Path = self.settings.ensure_data_dir() / "audit.log.1"
        self._lock = threading.Lock()
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max(1, self.settings.audit_max_buffer))

    def _redact(self, value: Any) -> Any:
        sensitive = {"password", "secret", "token", "api_key", "authorization", "key"}
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                key_lower = str(key).lower()
                if any(part in key_lower for part in sensitive):
                    cleaned[str(key)] = "***"
                else:
                    cleaned[str(key)] = self._redact(item)
            return cleaned
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        max_bytes = max(0, self.settings.audit_log_max_bytes)
        if max_bytes <= 0 or not self._path.exists():
            return
        if self._path.stat().st_size + incoming_bytes <= max_bytes:
            return
        if self._rotated_path.exists():
            self._rotated_path.unlink()
        self._path.rename(self._rotated_path)

    def _tail_file(self, n: int) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        lines: deque[str] = deque(maxlen=n)
        paths = [self._rotated_path, self._path]
        for path in paths:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        lines.append(line)
        out: list[dict[str, Any]] = []
        for raw in lines:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out.append(parsed)
        return out[-n:]

    def record(self, actor: str, action: str, **details: Any) -> dict[str, Any]:
        safe_details = self._redact(details)
        entry = {
            "ts": time.time(),
            "actor": actor,
            "action": action,
            "details": safe_details,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._buffer.append(dict(entry))
            encoded_len = len((line + "\n").encode("utf-8"))
            self._rotate_if_needed(encoded_len)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return entry

    def tail(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            from_file = self._tail_file(n)
            if from_file:
                return from_file
            return list(self._buffer)[-max(0, n):]
