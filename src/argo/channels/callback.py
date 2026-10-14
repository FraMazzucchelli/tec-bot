"""Pagina di presentazione di ARGO con il modulo "Fatti chiamare".

Chi prova ARGO inserisce il proprio cellulare e il codice di accesso: Twilio lo
chiama dal numero USA e la chiamata segue lo stesso flusso di /voice/incoming.
Per il tecnico è una chiamata in arrivo, quindi gratuita.

Protezioni contro abusi e spese impreviste:
- codice di accesso obbligatorio (CALLBACK_PASSWORD);
- solo cellulari italiani (+39 3...), più eventuali numeri in CALLBACK_NUMBERS;
- al massimo una chiamata ogni CALLBACK_COOLDOWN_SECONDS (predefinito 180) per numero
  e CALLBACK_MAX_PER_HOUR in totale;
- ogni chiamata si chiude dopo 5 minuti (TimeLimit di Twilio).
"""

from __future__ import annotations

import hmac
import logging
import re
import time
from html import escape
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger(__name__)
router = APIRouter()

TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "landing.html").read_text(
    encoding="utf-8"
)
ITALIAN_MOBILE = re.compile(r"^\+393\d{8,9}$")
CALL_TIME_LIMIT_SECONDS = 300


def normalize_number(raw: str) -> str:
    """'333 123 4567', '0039 333…', '+39 333-123…' → '+393331234567'."""
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        digits = "+39" + digits
    return digits


def _extra_numbers(settings) -> set[str]:
    return {normalize_number(n) for n in settings.callback_numbers.split(",") if n.strip()}


CODE_FIELD = """<div class="field">
          <label for="codice">Codice di accesso</label>
          <input id="codice" name="codice" type="password" autocomplete="off" required>
        </div>"""
INTRO_WITH_CODE = (
    "Inserisci il tuo cellulare e il codice di accesso che ti hanno dato: "
    "dopo pochi secondi ARGO ti telefona."
)
INTRO_OPEN = "Inserisci il tuo cellulare: dopo pochi secondi ARGO ti telefona."


def _render(
    settings, numero: str = "", message: str = "", ok: bool = False, status: int = 200
) -> HTMLResponse:
    box = ""
    if message:
        kind = "ok" if ok else "err"
        box = f'<div class="msg {kind}" role="status">{escape(message)}</div>'
    needs_code = bool(settings.callback_password)
    html = (
        TEMPLATE.replace("{{NUMERO}}", escape(numero, quote=True))
        .replace("{{MESSAGGIO}}", box)
        .replace("{{CAMPO_CODICE}}", CODE_FIELD if needs_code else "")
        .replace("{{INTRO_MODULO}}", INTRO_WITH_CODE if needs_code else INTRO_OPEN)
    )
    return HTMLResponse(html, status_code=status)


class CallLimiter:
    """Limiti in memoria: bastano per un solo server (si azzerano al riavvio)."""

    def __init__(self, per_number_seconds: int, max_per_hour: int):
        self.per_number_seconds = per_number_seconds
        self.max_per_hour = max_per_hour
        self.calls: list[tuple[float, str]] = []

    def check(self, number: str, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        self.calls = [(t, n) for t, n in self.calls if now - t < 3600]
        if any(n == number and now - t < self.per_number_seconds for t, n in self.calls):
            return "attendi"
        if self.max_per_hour and len(self.calls) >= self.max_per_hour:
            return "limite"
        return None

    def record(self, number: str, now: float | None = None) -> None:
        self.calls.append((time.time() if now is None else now, number))


def _limiter(request: Request) -> CallLimiter:
    state = request.app.state
    if not hasattr(state, "call_limiter"):
        state.call_limiter = CallLimiter(
            state.settings.callback_cooldown_seconds, state.settings.callback_max_per_hour
        )
    return state.call_limiter


# Esiti mostrati dopo l'invio del modulo. Dopo il POST la pagina fa un redirect
# a "/?esito=..." (Post/Redirect/Get): ricaricare la pagina non rifà la chiamata.
# Il numero non va mai nell'URL: è un dato personale.
OUTCOMES = {
    "chiamata": (True, "Ti sto chiamando: rispondi tra qualche secondo."),
    "disattivato": (False, "Le chiamate di prova sono disattivate."),
    "codice": (False, "Il codice di accesso non è corretto."),
    "numero": (False, "Inserisci un cellulare italiano, ad esempio 333 123 4567."),
    "attendi": (False, "Hai appena ricevuto una chiamata da ARGO: riprova tra qualche minuto."),
    "limite": (False, "ARGO ha già fatto molte chiamate nell'ultima ora: riprova più tardi."),
    "configurazione": (False, "Le chiamate di prova non sono configurate sul server."),
    "errore": (False, "La chiamata non è partita. Controlla il numero e riprova."),
}


def _outcome(code: str) -> RedirectResponse:
    return RedirectResponse(f"/?esito={code}#chiamami", status_code=303)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, esito: str = "") -> HTMLResponse:
    ok, message = OUTCOMES.get(esito, (False, ""))
    return _render(request.app.state.settings, "", message, ok=ok)


@router.get("/chiamami")
def old_link() -> RedirectResponse:
    return RedirectResponse("/#chiamami", status_code=307)


@router.post("/chiamami")
def callback_call(request: Request, numero: str = Form(""), codice: str = Form("")) -> RedirectResponse:
    s = request.app.state.settings
    if not s.callback_enabled:
        return _outcome("disattivato")
    # codice di accesso richiesto solo se CALLBACK_PASSWORD è impostata
    if s.callback_password and not hmac.compare_digest(codice.encode(), s.callback_password.encode()):
        log.warning("Fatti chiamare: codice di accesso errato")
        return _outcome("codice")

    number = normalize_number(numero)
    if not (ITALIAN_MOBILE.match(number) or number in _extra_numbers(s)):
        return _outcome("numero")

    limiter = _limiter(request)
    if (problem := limiter.check(number)) is not None:
        return _outcome(problem)

    if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_voice_from):
        return _outcome("configurazione")

    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Calls.json",
            data={
                "To": number,
                "From": s.twilio_voice_from,
                "Url": s.public_base_url + "/voice/incoming",
                "Method": "POST",
                "TimeLimit": str(CALL_TIME_LIMIT_SECONDS),
            },
            auth=(s.twilio_account_sid, s.twilio_auth_token),
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                detail = exc.response.json().get("message", "")
            except ValueError:
                detail = exc.response.text[:200]
        log.error("Fatti chiamare: Twilio ha rifiutato la chiamata: %s", detail or exc)
        return _outcome("errore")

    limiter.record(number)
    log.info("Fatti chiamare: chiamata avviata verso %s****", number[:-4])
    return _outcome("chiamata")
