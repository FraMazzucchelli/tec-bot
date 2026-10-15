"""Canale telefonico tramite Twilio Voice.

Twilio chiama /voice/incoming quando arriva una telefonata; ARGO risponde con
TwiML: saluta, ascolta (riconoscimento vocale in italiano) e invia il testo
riconosciuto a /voice/respond, in un ciclo fino a fine chiamata.
"""

import json
import logging
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ..security import verify_twilio
from .app import phone_for_call, remember_phone
from .relay import relay_twiml

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
def incoming(
    request: Request, CallSid: str = Form(""), From: str = Form(""), Telefono: str = Form("")
) -> Response:
    if From.startswith("client:"):  # chiamata dall'app: il cellulare arriva come parametro
        remember_phone(request.app.state, CallSid, Telefono, request.app.state.settings)
    if request.app.state.settings.voice_mode == "relay":
        return _twiml(relay_twiml(request.app.state.settings))
    return _say_and_listen(
        request,
        f"Ciao, sono {request.app.state.settings.spoken_name}, "
        "l'assistente automatico dell'assistenza tecnica. "
        "Se vuoi parlare con un collega dimmelo in qualsiasi momento. Qual è il problema?",
    )


@router.post("/respond")
def respond(
    request: Request,
    CallSid: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    Direction: str = Form("inbound"),
    SpeechResult: str = Form(""),
) -> Response:
    assistant = request.app.state.assistant
    # nelle chiamate partite da ARGO (pagina /chiamami) il tecnico è il destinatario
    technician = To if Direction.startswith("outbound") else From
    technician = phone_for_call(request.app.state, CallSid, technician)
    text = SpeechResult.strip()
    if not text:
        return _say_and_listen(request, "Scusa, non ho capito. Puoi ripetere?")
    if any(w in text.lower() for w in GOODBYE_WORDS):
        assistant.reset(f"voce:{CallSid}")
        return _twiml(_say(request, "Buon lavoro, ciao!") + "<Hangup/>")

    session = f"voce:{CallSid}"
    try:
        answer = assistant.reply(session, text, "voce", phone=technician)
    except Exception:
        log.exception("Errore durante la risposta vocale")
        answer = "Scusa, ho un problema tecnico in questo momento."
        if request.app.state.settings.human_support_number:
            return _transfer(request, answer + " Ti passo un collega.")
        return _say_and_listen(request, answer + " Riprova tra poco.")

    if assistant.take_handoff(session):
        assistant.reset(session)
        return _transfer(request, answer or "Ti passo un collega, resta in linea.")
    return _say_and_listen(request, answer)


def _transfer(request: Request, text: str) -> Response:
    """Dice la frase e trasferisce la chiamata all'operatore umano."""
    number = escape(request.app.state.settings.human_support_number)
    return _twiml(
        _say(request, text)
        + f'<Dial timeout="25" action="/voice/dial-status" method="POST">{number}</Dial>'
    )


@router.post("/dial-status")
def dial_status(request: Request, DialCallStatus: str = Form("")) -> Response:
    """Twilio chiama questo endpoint quando il trasferimento finisce."""
    if DialCallStatus == "completed":
        return _twiml("<Hangup/>")
    return _twiml(
        _say(request, "Il collega non è raggiungibile. Riprova più tardi oppure chiedimi di aprire un ticket. Ciao.")
        + "<Hangup/>"
    )


@router.post("/relay-fine")
def relay_end(
    request: Request, HandoffData: str = Form(""), SessionStatus: str = Form("")
) -> Response:
    """Twilio chiama questo endpoint quando la sessione ConversationRelay finisce.

    Con `handoffData` ARGO chiede di passare all'operatore o di salutare; se invece
    il tecnico ha riattaccato o c'è stato un errore, si chiude la chiamata.
    """
    try:
        data = json.loads(HandoffData) if HandoffData else {}
    except ValueError:
        data = {}
    action, text = data.get("azione"), data.get("testo", "")
    if action == "operatore" and request.app.state.settings.human_support_number:
        return _transfer(request, text or "Ti passo un collega, resta in linea.")
    if action in ("operatore", "chiudi"):
        return _twiml(_say(request, text or "Buon lavoro, ciao!") + "<Hangup/>")
    if SessionStatus and SessionStatus != "ended":
        log.warning("Sessione ConversationRelay terminata: %s", SessionStatus)
    return _twiml("<Hangup/>")
