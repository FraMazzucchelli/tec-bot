"""Chat sul sito, con due modalità.

- "info": domande sul servizio (cosa fa, come si attiva, prezzi) e richiesta di
  essere ricontattati. Aperta a tutti.
- "prova": la stessa ARGO di telefono e Telegram, ma con la sola ricerca nei
  manuali. Se è impostato CALLBACK_PASSWORD serve lo stesso codice di accesso del
  modulo "Fatti chiamare".

L'endpoint è pubblico e ogni messaggio costa una chiamata a Claude, quindi:
- testo massimo WEB_CHAT_MAX_CHARS caratteri;
- al massimo WEB_CHAT_MAX_MESSAGES messaggi per conversazione;
- al massimo WEB_CHAT_MAX_PER_HOUR messaggi all'ora in totale (0 = nessun limite).
"""

from __future__ import annotations

import hmac
import logging
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class WebMessage(BaseModel):
    modo: str = "info"
    sessione: str = ""
    testo: str = ""
    codice: str = ""


class WebChatLimiter:
    """Conteggi in memoria: bastano per un solo server (si azzerano al riavvio)."""

    def __init__(self, max_per_hour: int, max_per_session: int):
        self.max_per_hour = max_per_hour
        self.max_per_session = max_per_session
        self.times: list[float] = []
        self.per_session: dict[str, tuple[int, float]] = {}

    def check(self, session: str, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        self.times = [t for t in self.times if now - t < 3600]
        # le conversazioni ferme da più di un'ora non contano più
        self.per_session = {s: v for s, v in self.per_session.items() if now - v[1] < 3600}
        if self.max_per_session and self.per_session.get(session, (0, 0))[0] >= self.max_per_session:
            return "conversazione"
        if self.max_per_hour and len(self.times) >= self.max_per_hour:
            return "limite"
        return None

    def record(self, session: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.times.append(now)
        count = self.per_session.get(session, (0, now))[0]
        self.per_session[session] = (count + 1, now)


ERRORS = {
    "disattivata": (403, "La chat è disattivata."),
    "modo": (400, "Modalità non valida."),
    "sessione": (400, "Conversazione non valida: ricarica la pagina."),
    "vuoto": (400, "Scrivi una domanda."),
    "lungo": (400, "Il messaggio è troppo lungo: accorcialo e riprova."),
    "codice": (403, "Per la prova sui manuali serve il codice di accesso corretto."),
    "conversazione": (
        429,
        "Questa conversazione ha raggiunto il numero massimo di messaggi: iniziane una nuova.",
    ),
    "limite": (429, "La chat ha ricevuto molti messaggi nell'ultima ora: riprova più tardi."),
    "errore": (502, "Non riesco a rispondere in questo momento. Riprova tra poco."),
}


def _error(code: str) -> JSONResponse:
    status, message = ERRORS[code]
    return JSONResponse({"errore": message, "codice": code}, status_code=status)


def _limiter(request: Request) -> WebChatLimiter:
    state = request.app.state
    if not hasattr(state, "web_chat_limiter"):
        s = state.settings
        state.web_chat_limiter = WebChatLimiter(s.web_chat_max_per_hour, s.web_chat_max_messages)
    return state.web_chat_limiter


@router.post("/chat/web")
def web_chat(message: WebMessage, request: Request) -> JSONResponse:
    state = request.app.state
    s = state.settings
    if not s.web_chat_enabled:
        return _error("disattivata")
    if message.modo not in ("info", "prova"):
        return _error("modo")
    if not SESSION_ID.match(message.sessione):
        return _error("sessione")
    text = message.testo.strip()
    if not text:
        return _error("vuoto")
    if len(text) > s.web_chat_max_chars:
        return _error("lungo")
    if message.modo == "prova" and s.callback_password and not hmac.compare_digest(
        message.codice.encode(), s.callback_password.encode()
    ):
        return _error("codice")

    session = f"web-{message.modo}:{message.sessione}"
    limiter = _limiter(request)
    if (problem := limiter.check(session)) is not None:
        return _error(problem)
    limiter.record(session)

    try:
        if message.modo == "info":
            answer = state.info_assistant.reply(session, text, "info")
        else:
            answer = state.assistant.reply(session, text, "web")
    except Exception:
        log.exception("Errore nella chat del sito (%s)", message.modo)
        return _error("errore")
    return JSONResponse({"risposta": answer})


@router.post("/chat/web/nuova")
def new_conversation(message: WebMessage, request: Request) -> JSONResponse:
    """Cancella subito la conversazione quando il visitatore ne inizia una nuova."""
    if message.modo in ("info", "prova") and SESSION_ID.match(message.sessione):
        session = f"web-{message.modo}:{message.sessione}"
        target = request.app.state.info_assistant if message.modo == "info" else request.app.state.assistant
        target.reset(session)
    return JSONResponse({"ok": True})
