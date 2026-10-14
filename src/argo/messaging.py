"""Invio di messaggi WhatsApp tramite l'API Messages di Twilio."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Protocol

import httpx

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger(__name__)

WHATSAPP_MAX_CHARS = 1500  # Twilio accetta al massimo 1600 caratteri per messaggio
# La sandbox WhatsApp invia al massimo un messaggio ogni 3 secondi
SECONDS_BETWEEN_PARTS = 3.2


class Messenger(Protocol):
    def send_whatsapp(self, to_number: str, body: str) -> None: ...


class TwilioWhatsApp:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        background: Callable[[Callable[[], None]], None] | None = None,
    ):
        self.settings = settings
        self.client = client or httpx.Client(timeout=15)
        self.sleep = sleep
        self.background = background or (lambda fn: threading.Thread(target=fn, daemon=True).start())

    def _post(self, to: str, part: str) -> None:
        s = self.settings
        sender = s.twilio_whatsapp_from
        if not sender.startswith("whatsapp:"):
            sender = f"whatsapp:{sender}"
        response = self.client.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
            data={"From": sender, "To": to, "Body": part},
            auth=(s.twilio_account_sid, s.twilio_auth_token),
        )
        if response.status_code >= 400:
            raise WhatsAppError(_twilio_error(response))

    def send_whatsapp(self, to_number: str, body: str) -> None:
        """Invia il primo messaggio subito (così un errore arriva al tecnico) e gli
        eventuali altri pezzi in background, distanziati per rispettare i limiti della
        sandbox senza far aspettare la telefonata."""
        to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        first, *rest = split_message(body)
        self._post(to, first)
        if not rest:
            return

        def send_rest() -> None:
            for part in rest:
                self.sleep(SECONDS_BETWEEN_PARTS)
                try:
                    self._post(to, part)
                except Exception:
                    log.exception("WhatsApp: invio di una parte successiva fallito")
                    return

        self.background(send_rest)


class WhatsAppError(Exception):
    """Errore restituito da Twilio, con il messaggio leggibile."""


def _twilio_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    code = data.get("code")
    hint = {
        21608: "il numero non è verificato nell'account di prova",
        63007: "il mittente TWILIO_WHATSAPP_FROM non è un numero WhatsApp valido (deve essere quello della sandbox)",
        63015: "il destinatario non ha fatto il 'join' alla sandbox WhatsApp, o l'iscrizione è scaduta",
        21211: "numero destinatario non valido",
    }.get(code, "")
    message = data.get("message", "")
    return f"Twilio {code}: {message}" + (f" ({hint})" if hint else "")


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
