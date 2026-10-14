"""Canali chat: Telegram (webhook Bot API) e WhatsApp (tramite Twilio)."""

import logging
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/telegram/webhook")
def telegram_webhook(update: dict, request: Request) -> dict:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    chat_id = (message.get("chat") or {}).get("id")
    if not text or chat_id is None:
        return {"ok": True}

    assistant = request.app.state.assistant
    session = f"telegram:{chat_id}"
    if text in ("/start", "/nuovo"):
        assistant.reset(session)
        answer = "Ciao, sono ARGO. Scrivimi il problema sull'impianto e ti aiuto."
    else:
        answer = assistant.reply(session, text, "telegram")

    token = request.app.state.settings.telegram_bot_token
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": answer},
            timeout=15,
        ).raise_for_status()
    except httpx.HTTPError:
        log.exception("Invio messaggio Telegram fallito")
    return {"ok": True}


@router.post("/whatsapp/webhook")
def whatsapp_webhook(request: Request, From: str = Form(""), Body: str = Form("")) -> Response:
    assistant = request.app.state.assistant
    text = Body.strip()
    answer = (
        assistant.reply(f"whatsapp:{From}", text, "whatsapp")
        if text
        else "Scrivimi il problema sull'impianto e ti aiuto."
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Message>{escape(answer)}</Message></Response>"
    )
    return Response(content=xml, media_type="application/xml")
