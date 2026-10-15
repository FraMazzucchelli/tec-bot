"""Canale telefonico con Twilio ConversationRelay (VOICE_MODE=relay).

Twilio si occupa di riconoscimento vocale e sintesi in streaming e parla con ARGO
su un websocket: a ogni frase del tecnico arriva un messaggio `prompt`, ARGO
risponde con messaggi `text`, inviati a pezzi mentre Claude scrive (streaming):
la voce parte con la prima frase. Rispetto a <Gather>/<Say>:
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
from .app import phone_for_call

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
    if settings.relay_speech_model:
        attrs["speechModel"] = settings.relay_speech_model
    if settings.relay_eot_threshold:
        attrs["eotThreshold"] = settings.relay_eot_threshold
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


class _Turn:
    """Una risposta di ARGO in corso: si può interrompere se il tecnico parla sopra."""

    def __init__(self) -> None:
        self.interrupted = False
        self.task: asyncio.Task | None = None


async def _answer(websocket, settings, assistant, session, technician, text, turn: _Turn) -> None:
    """Genera la risposta in un thread e la manda a Twilio a pezzi, mentre arriva."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    def on_text(chunk: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, chunk)

    def work() -> str:
        try:
            return assistant.converse(session, text, "voce", technician, on_text=on_text)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    future = asyncio.ensure_future(run_in_threadpool(work))
    sent = False  # qualcosa è già stato detto in questo turno
    held: list[str] = []  # testo trattenuto quando ARGO sta passando a un operatore
    first = True
    while True:
        try:
            timeout = FILLER_AFTER_SECONDS if first and not sent else None
            chunk = await asyncio.wait_for(queue.get(), timeout)
        except asyncio.TimeoutError:
            if not turn.interrupted:
                await _send_text(websocket, FILLER_TEXT)
            first = False
            continue
        first = False
        if chunk is done:
            break
        if turn.interrupted:
            continue  # il tecnico ha parlato sopra: il resto non si dice
        if assistant.handoff_pending(session):
            held.append(chunk)  # lo dirà /voice/relay-fine prima di trasferire
            continue
        await websocket.send_json({"type": "text", "token": chunk, "last": False})
        sent = True

    try:
        answer = await future
    except Exception:
        log.exception("Errore durante la risposta vocale (ConversationRelay)")
        answer = "Scusa, ho un problema tecnico in questo momento."
        if settings.human_support_number:
            await _end(websocket, "operatore", answer + " Ti passo un collega.")
            return
        await _send_text(websocket, answer + " Riprova tra poco.")
        return

    if assistant.take_handoff(session):
        assistant.reset(session)
        await _end(websocket, "operatore", "".join(held).strip() or "Ti passo un collega, resta in linea.")
        return
    if turn.interrupted:
        return
    if sent:
        await websocket.send_json({"type": "text", "token": "", "last": True})
    else:
        await _send_text(websocket, answer)  # es. risposta di ripiego senza streaming


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
    turn = _Turn()
    try:
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")

            if kind == "setup":
                session = f"voce:{message.get('callSid', '')}"
                direction = message.get("direction", "inbound")
                # nelle chiamate partite da ARGO (pagina /chiamami) il tecnico è il destinatario
                technician = message.get("to" if direction.startswith("outbound") else "from", "")
                technician = phone_for_call(app.state, message.get("callSid", ""), technician)
                continue

            if kind == "interrupt":
                turn.interrupted = True
                continue

            if kind == "error":
                log.error("ConversationRelay: %s", message.get("description", ""))
                continue

            if kind != "prompt" or not message.get("last", True):
                continue  # dtmf, frasi parziali: nulla da fare

            text = (message.get("voicePrompt") or "").strip()
            # una risposta alla volta: la cronologia della chiamata è una sola
            if turn.task is not None and not turn.task.done():
                turn.interrupted = True
                try:
                    await turn.task
                except Exception:
                    log.exception("Errore nella risposta interrotta")

            if not text:
                await _send_text(websocket, "Scusa, non ho capito. Puoi ripetere?")
                continue
            if any(w in text.lower() for w in GOODBYE_WORDS):
                assistant.reset(session)
                await _end(websocket, "chiudi", "Buon lavoro, ciao!")
                return

            turn = _Turn()
            turn.task = asyncio.ensure_future(
                _answer(websocket, settings, assistant, session, technician, text, turn)
            )
    except WebSocketDisconnect:
        pass  # il tecnico ha riattaccato
    finally:
        if turn.task is not None and not turn.task.done():
            turn.interrupted = True
            try:
                await turn.task
            except Exception:
                pass
