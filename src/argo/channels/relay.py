"""Canale telefonico con Twilio ConversationRelay (VOICE_MODE=relay).

Twilio si occupa di riconoscimento vocale e sintesi in streaming e parla con ARGO
su un websocket: a ogni frase del tecnico arriva un messaggio `prompt`, ARGO
risponde con messaggi `text`. Rispetto a <Gather>/<Say>:
- il tecnico può interrompere il bot mentre parla;
- non c'è il limite di ~15 secondi per la risposta del webhook;
- si possono usare voci più naturali (ElevenLabs, Google, Amazon).

Trasferimento all'operatore e saluti finali: ARGO chiude la sessione con un
messaggio `end` e `handoffData`; Twilio chiama allora l'URL `action` di <Connect>
(/voice/relay-fine in voice.py), che dice la frase e fa <Dial> o <Hangup>.

Messaggi: https://www.twilio.com/docs/voice/conversationrelay/websocket-messages
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..security import twilio_signature

log = logging.getLogger(__name__)
router = APIRouter()

GOODBYE_WORDS = ("grazie ciao", "arrivederci", "basta così", "ho finito")
# se Claude ci mette di più (es. ricerca nei manuali), il tecnico sente questa frase
FILLER_AFTER_SECONDS = 1.5
FILLER_TEXT = "Un attimo, controllo."


def relay_twiml(settings) -> str:
    """TwiML che collega la chiamata al websocket di ARGO."""
    base = settings.public_base_url
    ws_url = "wss://" + base.split("://", 1)[-1] + "/voice/relay"
    attrs = {
        "url": ws_url,
        "language": "it-IT",
        "ttsProvider": settings.relay_tts_provider,
        "transcriptionProvider": settings.relay_transcription_provider,
        "interruptible": "any",
        "welcomeGreeting": (
            f"Ciao, sono {settings.spoken_name}, l'assistente automatico dell'assistenza tecnica. "
            "Se vuoi parlare con un collega dimmelo in qualsiasi momento. Qual è il problema?"
        ),
    }
    if settings.relay_voice:
        attrs["voice"] = settings.relay_voice
    if settings.relay_hints:
        attrs["hints"] = settings.relay_hints
    relay = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items())
    action = quoteattr(base + "/voice/relay-fine")
    return (
        f'<Connect action={action} method="POST"><ConversationRelay {relay} /></Connect>'
    )


def websocket_signature_ok(settings, websocket: WebSocket) -> bool:
    if settings.skip_signature_check:
        return True
    if not settings.twilio_auth_token or not settings.public_base_url:
        return False
    received = websocket.headers.get("x-twilio-signature", "")
    host = settings.public_base_url.split("://", 1)[-1]
    path = websocket.url.path + (f"?{websocket.url.query}" if websocket.url.query else "")
    # Twilio firma l'URL del websocket: accettiamo sia wss:// sia https://
    for scheme in ("wss", "https"):
        expected = twilio_signature(settings.twilio_auth_token, f"{scheme}://{host}{path}", [])
        if hmac.compare_digest(expected, received):
            return True
    return False


async def _send_text(websocket: WebSocket, text: str) -> None:
    await websocket.send_json({"type": "text", "token": text, "last": True})


async def _end(websocket: WebSocket, action: str, text: str) -> None:
    await websocket.send_json(
        {"type": "end", "handoffData": json.dumps({"azione": action, "testo": text})}
    )


@router.websocket("/voice/relay")
async def relay(websocket: WebSocket) -> None:
    app = websocket.app
    settings = app.state.settings
    if not websocket_signature_ok(settings, websocket):
        log.warning("Firma Twilio non valida sul websocket ConversationRelay")
        await websocket.close(code=1008)
        return
    await websocket.accept()

    assistant = app.state.assistant
    session = ""
    technician = ""
    try:
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")

            if kind == "setup":
                session = f"voce:{message.get('callSid', '')}"
                direction = message.get("direction", "inbound")
                # nelle chiamate partite da ARGO (pagina /chiamami) il tecnico è il destinatario
                technician = message.get("to" if direction.startswith("outbound") else "from", "")
                continue

            if kind == "error":
                log.error("ConversationRelay: %s", message.get("description", ""))
                continue

            if kind != "prompt" or not message.get("last", True):
                continue  # interrupt, dtmf, frasi parziali: nulla da fare

            text = (message.get("voicePrompt") or "").strip()
            if not text:
                await _send_text(websocket, "Scusa, non ho capito. Puoi ripetere?")
                continue
            if any(w in text.lower() for w in GOODBYE_WORDS):
                assistant.reset(session)
                await _end(websocket, "chiudi", "Buon lavoro, ciao!")
                return

            work = asyncio.ensure_future(
                run_in_threadpool(assistant.reply, session, text, "voce", technician)
            )
            try:
                done, _ = await asyncio.wait({work}, timeout=FILLER_AFTER_SECONDS)
                if not done:
                    await _send_text(websocket, FILLER_TEXT)
                answer = await work
            except Exception:
                log.exception("Errore durante la risposta vocale (ConversationRelay)")
                answer = "Scusa, ho un problema tecnico in questo momento."
                if settings.human_support_number:
                    await _end(websocket, "operatore", answer + " Ti passo un collega.")
                    return
                await _send_text(websocket, answer + " Riprova tra poco.")
                continue

            if assistant.take_handoff(session):
                assistant.reset(session)
                await _end(websocket, "operatore", answer or "Ti passo un collega, resta in linea.")
                return
            await _send_text(websocket, answer)
    except WebSocketDisconnect:
        pass  # il tecnico ha riattaccato
    finally:
        if session:
            log.info("Fine sessione ConversationRelay")
