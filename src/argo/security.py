"""Verifica che le richieste ai webhook arrivino davvero da Twilio e Telegram.

Twilio firma ogni richiesta con l'header `X-Twilio-Signature`:
HMAC-SHA1 (chiave = Auth Token) dell'URL completo del webhook seguito da tutti i
parametri POST ordinati per nome (nome+valore concatenati), codificato in base64.

Telegram invece rimanda in ogni chiamata il `secret_token` scelto da noi in
`setWebhook`, nell'header `X-Telegram-Bot-Api-Secret-Token`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Iterable

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


def twilio_signature(auth_token: str, url: str, params: Iterable[tuple[str, str]]) -> str:
    payload = url + "".join(k + v for k, v in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


async def verify_twilio(request: Request) -> None:
    settings = request.app.state.settings
    if settings.skip_signature_check:
        return
    if not settings.twilio_auth_token or not settings.public_base_url:
        raise HTTPException(500, "TWILIO_AUTH_TOKEN e PUBLIC_BASE_URL sono obbligatori")

    # Usiamo l'URL pubblico configurato: dietro proxy o tunnel request.url
    # avrebbe host/schema interni e la firma non tornerebbe.
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    form = await request.form()
    expected = twilio_signature(
        settings.twilio_auth_token, url, [(k, str(v)) for k, v in form.multi_items()]
    )
    received = request.headers.get("X-Twilio-Signature", "")
    if not hmac.compare_digest(expected, received):
        log.warning("Firma Twilio non valida su %s", request.url.path)
        raise HTTPException(403, "Firma non valida")


async def verify_telegram(request: Request) -> None:
    settings = request.app.state.settings
    if settings.skip_signature_check:
        return
    if not settings.telegram_webhook_secret:
        raise HTTPException(500, "TELEGRAM_WEBHOOK_SECRET è obbligatorio")
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(settings.telegram_webhook_secret, received):
        log.warning("Secret token Telegram non valido")
        raise HTTPException(403, "Token non valido")
