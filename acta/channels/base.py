"""Channel hub: run the ACTA pipeline for inbound messages from any channel."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from acta.logging_config import get_logger
from acta.orchestrator import Orchestrator
from acta.schemas import UserRequest

log = get_logger("channels")


class RecentEventDeduper:
    """Keep a bounded set of recent inbound IDs for webhook idempotency."""

    def __init__(self, max_size: int) -> None:
        self.max_size = max(1, max_size)
        self._queue: deque[str] = deque()
        self._seen: set[str] = set()

    def remember(self, event_id: str) -> bool:
        """Return True only when this event ID is seen for the first time."""
        if event_id in self._seen:
            return False
        self._queue.append(event_id)
        self._seen.add(event_id)
        while len(self._queue) > self.max_size:
            expired = self._queue.popleft()
            self._seen.discard(expired)
        return True


@dataclass
class IncomingMessage:
    channel: str  # "telegram" | "whatsapp" | ...
    sender_id: str  # chat id / phone number
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def user_id(self) -> str:
        # Namespacing keeps per-channel users distinct in memory.
        return f"{self.channel}:{self.sender_id}"


class ChannelHub:
    """Bridges external messaging channels to the orchestrator."""

    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orchestrator = orchestrator

    def handle(self, msg: IncomingMessage) -> str:
        """Process one inbound message and return the answer text."""
        if not msg.text.strip():
            return ""
        request = UserRequest(
            user_id=msg.user_id,
            text=msg.text,
            metadata={"channel": msg.channel, **msg.metadata},
        )
        log.info("[%s] %s: %s", msg.channel, msg.sender_id, msg.text[:80])
        response = self.orchestrator.run(request)
        return response.answer or ""
