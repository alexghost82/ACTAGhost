"""WhatsApp channel adapter (Meta WhatsApp Cloud API).

Inbound messages arrive via a webhook (verified with a token); replies are sent
through the Cloud API graph endpoint. Configure with ``ACTA_WHATSAPP_TOKEN``,
``ACTA_WHATSAPP_PHONE_ID`` and ``ACTA_WHATSAPP_VERIFY_TOKEN``.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from acta.channels.base import ChannelHub, IncomingMessage, RecentEventDeduper
from acta.config import Settings, get_settings
from acta.logging_config import get_logger
from acta.schemas import Modality

log = get_logger("channels.whatsapp")
_GRAPH = "https://graph.facebook.com/v20.0"


class WhatsAppChannel:
    def __init__(self, hub: ChannelHub, settings: Settings | None = None) -> None:
        self.hub = hub
        self.settings = settings or get_settings()
        self.token = self.settings.whatsapp_token
        self.phone_id = self.settings.whatsapp_phone_id
        self.verify_token = self.settings.whatsapp_verify_token
        self.app_secret = self.settings.whatsapp_app_secret
        self._dedupe = RecentEventDeduper(self.settings.inbound_dedupe_window_size)
        self._allowed_numbers = {str(x).strip() for x in self.settings.whatsapp_allowed_numbers if str(x).strip()}
        if not self._allowed_numbers:
            log.warning("WhatsApp sender allowlist is empty; all numbers are accepted")

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
                    sender = message.get("from")
                    msg_type = message.get("type")
                    text = message.get("text", {}).get("body", "")
                    message_id = message.get("id")
                    attachments: list[dict[str, Any]] = []
                    modality = Modality.TEXT
                    if msg_type == "image":
                        image = message.get("image", {})
                        attachments.append(
                            {
                                "type": "image",
                                "platform": "whatsapp",
                                "media_id": image.get("id"),
                                "mime_type": image.get("mime_type"),
                                "caption": image.get("caption"),
                                "url": image.get("link"),
                            }
                        )
                        text = text or image.get("caption", "")
                        modality = Modality.IMAGE
                    elif msg_type in {"audio", "voice"}:
                        audio = message.get("audio", {})
                        attachments.append(
                            {
                                "type": "audio",
                                "platform": "whatsapp",
                                "media_id": audio.get("id"),
                                "mime_type": audio.get("mime_type"),
                                "url": audio.get("link"),
                            }
                        )
                        modality = Modality.VOICE

                    if sender and (text or attachments):
                        out.append(
                            IncomingMessage(
                                channel="whatsapp",
                                sender_id=sender,
                                text=text,
                                modality=modality,
                                attachments=attachments,
                                metadata={"message_id": message_id},
                            )
                        )
        return out

    def handle_webhook(self, payload: dict[str, Any]) -> int:
        messages = self.parse_webhook(payload)
        processed = 0
        for msg in messages:
            message_id = msg.metadata.get("message_id")
            if message_id and not self._dedupe.remember(str(message_id)):
                log.debug("Skipping duplicate whatsapp message id=%s", message_id)
                continue
            if not self._is_sender_allowed(msg.sender_id):
                log.warning("Dropping whatsapp message from non-allowlisted sender: %s", msg.sender_id)
                continue
            try:
                answer = self.hub.handle(msg)
                self.send_message(msg.sender_id, answer)
                processed += 1
            except Exception:
                log.exception("failed handling whatsapp message")
        return processed

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        if not self.app_secret:
            return True
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(
            self.app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        actual = signature_header.split("=", 1)[1].strip()
        return hmac.compare_digest(expected, actual)

    def _is_sender_allowed(self, sender_id: str) -> bool:
        if not self._allowed_numbers:
            return True
        return sender_id in self._allowed_numbers

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
