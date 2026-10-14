"""Invio di messaggi WhatsApp tramite l'API Messages di Twilio."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from .config import Settings

WHATSAPP_MAX_CHARS = 1500  # Twilio accetta al massimo 1600 caratteri per messaggio


class Messenger(Protocol):
    def send_whatsapp(self, to_number: str, body: str) -> None: ...


class TwilioWhatsApp:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=15)

    def send_whatsapp(self, to_number: str, body: str) -> None:
        s = self.settings
        to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        for part in split_message(body):
            response = self.client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                data={"From": s.twilio_whatsapp_from, "To": to, "Body": part},
                auth=(s.twilio_account_sid, s.twilio_auth_token),
            )
            response.raise_for_status()


def split_message(text: str, limit: int = WHATSAPP_MAX_CHARS) -> list[str]:
    """Divide un testo lungo in più messaggi, tagliando sulle righe quando possibile."""
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                parts.append(current.rstrip())
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            parts.append(current.rstrip())
            current = ""
        current += line
    if current.strip():
        parts.append(current.rstrip())
    return parts
