"""Canale telefonico tramite Twilio Voice.

Twilio chiama /voice/incoming quando arriva una telefonata; ARGO risponde con
TwiML: saluta, ascolta (riconoscimento vocale in italiano) e invia il testo
riconosciuto a /voice/respond, in un ciclo fino a fine chiamata.
"""

import logging
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ..security import verify_twilio

log = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", dependencies=[Depends(verify_twilio)])

GOODBYE_WORDS = ("grazie ciao", "arrivederci", "basta così", "ho finito")


def _twiml(body: str) -> Response:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
    return Response(content=xml, media_type="application/xml")


def _say(request: Request, text: str) -> str:
    voice = quoteattr(request.app.state.settings.twilio_voice)
    return f'<Say language="it-IT" voice={voice}>{escape(text)}</Say>'


def _say_and_listen(request: Request, text: str) -> Response:
    return _twiml(
        '<Gather input="speech" language="it-IT" speechTimeout="auto" '
        'action="/voice/respond" method="POST">'
        f"{_say(request, text)}"
        "</Gather>"
        f"{_say(request, 'Non ho sentito nulla. A presto.')}"
        "<Hangup/>"
    )


@router.post("/incoming")
def incoming(request: Request) -> Response:
    return _say_and_listen(
        request, "Ciao, sono ARGO, l'assistenza tecnica. Dimmi pure il problema."
    )


@router.post("/respond")
def respond(
    request: Request,
    CallSid: str = Form(""),
    From: str = Form(""),
    SpeechResult: str = Form(""),
) -> Response:
    assistant = request.app.state.assistant
    text = SpeechResult.strip()
    if not text:
        return _say_and_listen(request, "Scusa, non ho capito. Puoi ripetere?")
    if any(w in text.lower() for w in GOODBYE_WORDS):
        assistant.reset(f"voce:{CallSid}")
        return _twiml(_say(request, "Buon lavoro, ciao!") + "<Hangup/>")

    try:
        answer = assistant.reply(f"voce:{CallSid}", f"[chiamata da {From}] {text}", "voce")
    except Exception:
        log.exception("Errore durante la risposta vocale")
        answer = "Scusa, ho un problema tecnico in questo momento. Riprova tra poco."
    return _say_and_listen(request, answer)
