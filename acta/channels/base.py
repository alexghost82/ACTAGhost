"""Channel hub: run the ACTA pipeline for inbound messages from any channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acta.logging_config import get_logger
from acta.orchestrator import Orchestrator
from acta.schemas import UserRequest

log = get_logger("channels")


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
