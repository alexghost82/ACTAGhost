"""Messaging channels: talk to ACTA from WhatsApp and Telegram.

A :class:`ChannelHub` runs the full cognitive pipeline for an inbound message
and returns the answer text. Each channel adapter only handles transport
(parsing webhooks/updates and sending replies).
"""

from acta.channels.base import ChannelHub, IncomingMessage
from acta.channels.telegram import TelegramChannel
from acta.channels.whatsapp import WhatsAppChannel

__all__ = ["ChannelHub", "IncomingMessage", "TelegramChannel", "WhatsAppChannel"]
