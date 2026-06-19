"""Telegram channel adapter (Bot API).

Supports both long-polling (no public URL needed, ideal for local/desktop use)
and webhook mode. Uses only ``httpx`` — no extra dependencies.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from acta.channels.base import ChannelHub, IncomingMessage
from acta.config import Settings, get_settings
from acta.logging_config import get_logger

log = get_logger("channels.telegram")


class TelegramChannel:
    def __init__(self, hub: ChannelHub, settings: Settings | None = None) -> None:
        self.hub = hub
        self.settings = settings or get_settings()
        self.token = self.settings.telegram_bot_token
        self._base = f"https://api.telegram.org/bot{self.token}"
        self._offset = 0
        self._running = False

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    # -- transport --------------------------------------------------------- #
    def send_message(self, chat_id: str | int, text: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "telegram token not configured"}
        resp = httpx.post(
            f"{self._base}/sendMessage",
            json={"chat_id": chat_id, "text": text or "…"},
            timeout=30,
        )
        return resp.json()

    def parse_update(self, update: dict[str, Any]) -> IncomingMessage | None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return None
        text = message.get("text")
        chat = message.get("chat", {})
        if not text or "id" not in chat:
            return None
        frm = message.get("from", {})
        return IncomingMessage(
            channel="telegram",
            sender_id=str(chat["id"]),
            text=text,
            metadata={"username": frm.get("username"), "first_name": frm.get("first_name")},
        )

    def handle_update(self, update: dict[str, Any]) -> None:
        msg = self.parse_update(update)
        if msg is None:
            return
        answer = self.hub.handle(msg)
        self.send_message(msg.sender_id, answer)

    # -- long polling ------------------------------------------------------ #
    def poll_forever(self, interval: float = 1.0) -> None:
        if not self.enabled:
            log.warning("Telegram polling skipped: no token configured")
            return
        self._running = True
        log.info("Telegram long-polling started")
        while self._running:
            try:
                resp = httpx.get(
                    f"{self._base}/getUpdates",
                    params={"offset": self._offset, "timeout": 25},
                    timeout=40,
                )
                for update in resp.json().get("result", []):
                    self._offset = max(self._offset, update["update_id"] + 1)
                    try:
                        self.handle_update(update)
                    except Exception:
                        log.exception("failed handling telegram update")
            except Exception:
                log.exception("telegram polling error")
                time.sleep(3)
            time.sleep(interval)

    def stop(self) -> None:
        self._running = False


def run_cli() -> None:
    """Console entrypoint: ``acta-telegram`` — run only the Telegram poller."""
    from acta.agents import AgentServices
    from acta.channels.base import ChannelHub
    from acta.logging_config import configure_logging
    from acta.orchestrator import Orchestrator

    settings = get_settings()
    configure_logging(settings.log_level)
    hub = ChannelHub(Orchestrator(AgentServices.build(settings)))
    channel = TelegramChannel(hub, settings)
    if not channel.enabled:
        log.error("ACTA_TELEGRAM_BOT_TOKEN is not set")
        return
    channel.poll_forever()
