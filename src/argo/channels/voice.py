"""Canale telefonico tramite Twilio Voice.

Twilio chiama /voice/incoming quando arriva una telefonata; ARGO risponde con
TwiML: saluta, ascolta (riconoscimento vocale in italiano) e invia il testo
riconosciuto a /voice/respond, in un ciclo fino a fine chiamata.
"""

from xml.sax.saxutils import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

router = APIRouter(prefix="/voice")

VOICE = "Polly.Bianca"  # voce italiana disponibile su Twilio
GOODBYE_WORDS = ("grazie ciao", "arrivederci", "basta così", "ho finito")


def _twiml(body: str) -> Response:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
    return Response(content=xml, media_type="application/xml")


def _say_and_listen(text: str) -> Response:
    return _twiml(
        '<Gather input="speech" language="it-IT" speechTimeout="auto" '
        'action="/voice/respond" method="POST">'
        f'<Say language="it-IT" voice="{VOICE}">{escape(text)}</Say>'
        "</Gather>"
        f'<Say language="it-IT" voice="{VOICE}">Non ho sentito nulla. A presto.</Say>'
        "<Hangup/>"
    )


@router.post("/incoming")
def incoming(CallSid: str = Form(""), From: str = Form("")) -> Response:
    return _say_and_listen("Ciao, sono ARGO, l'assistenza tecnica. Dimmi pure il problema.")


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
        return _say_and_listen("Scusa, non ho capito. Puoi ripetere?")
    if any(w in text.lower() for w in GOODBYE_WORDS):
        assistant.reset(f"voce:{CallSid}")
        return _twiml(f'<Say language="it-IT" voice="{VOICE}">Buon lavoro, ciao!</Say><Hangup/>')

    answer = assistant.reply(f"voce:{CallSid}", f"[chiamata da {From}] {text}", "voce")
    return _say_and_listen(answer)
