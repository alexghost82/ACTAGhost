"""WhatsApp channel adapter (Meta WhatsApp Cloud API).

Inbound messages arrive via a webhook (verified with a token); replies are sent
through the Cloud API graph endpoint. Configure with ``ACTA_WHATSAPP_TOKEN``,
``ACTA_WHATSAPP_PHONE_ID`` and ``ACTA_WHATSAPP_VERIFY_TOKEN``.
"""

from __future__ import annotations

from typing import Any

import httpx

from acta.channels.base import ChannelHub, IncomingMessage
from acta.config import Settings, get_settings
from acta.logging_config import get_logger

log = get_logger("channels.whatsapp")
_GRAPH = "https://graph.facebook.com/v20.0"


class WhatsAppChannel:
    def __init__(self, hub: ChannelHub, settings: Settings | None = None) -> None:
        self.hub = hub
        self.settings = settings or get_settings()
        self.token = self.settings.whatsapp_token
        self.phone_id = self.settings.whatsapp_phone_id
        self.verify_token = self.settings.whatsapp_verify_token

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.phone_id)

    # -- webhook verification (GET) ---------------------------------------- #
    def verify(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        if mode == "subscribe" and token == self.verify_token:
            return challenge
        return None

    # -- inbound (POST) ---------------------------------------------------- #
    def parse_webhook(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        out: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    if message.get("type") != "text":
                        continue
                    sender = message.get("from")
                    text = message.get("text", {}).get("body", "")
                    if sender and text:
                        out.append(IncomingMessage(channel="whatsapp", sender_id=sender, text=text))
        return out

    def handle_webhook(self, payload: dict[str, Any]) -> int:
        messages = self.parse_webhook(payload)
        for msg in messages:
            try:
                answer = self.hub.handle(msg)
                self.send_message(msg.sender_id, answer)
            except Exception:
                log.exception("failed handling whatsapp message")
        return len(messages)

    # -- outbound ---------------------------------------------------------- #
    def send_message(self, to: str, text: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "whatsapp not configured"}
        resp = httpx.post(
            f"{_GRAPH}/{self.phone_id}/messages",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text or "…"},
            },
            timeout=30,
        )
        try:
            return resp.json()
        except Exception:
            return {"ok": resp.is_success, "status": resp.status_code}
