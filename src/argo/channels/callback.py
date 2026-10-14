"""Pagina "Chiamami": ARGO telefona a un numero autorizzato.

Utile quando chi prova ARGO non può chiamare il numero Twilio (es. operatore che
blocca le chiamate internazionali): la chiamata parte da Twilio verso il tecnico,
che la riceve gratis, e segue lo stesso flusso di /voice/incoming.

Protezioni: password (CALLBACK_PASSWORD) e solo numeri in CALLBACK_NUMBERS, così
nessuno può usare la pagina per far chiamare numeri a caso a spese del credito.
"""

import hmac
import logging
from html import escape

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)
router = APIRouter()

PAGE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARGO – Chiamami</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:420px;margin:40px auto;padding:0 16px;color:#1b1b1b;background:#fafafa}}
 h1{{font-size:1.4rem}} label{{display:block;margin:14px 0 4px}}
 input,select,button{{width:100%;padding:10px;font-size:1rem;box-sizing:border-box;border:1px solid #bbb;border-radius:8px}}
 button{{margin-top:18px;background:#1f5eff;color:#fff;border:none;cursor:pointer}}
 .msg{{margin-top:18px;padding:10px;border-radius:8px;background:#eef3ff}}
 .demo{{margin:18px 0;padding:12px 14px;border-radius:8px;background:#f1f7ee;border:1px solid #cfe3c6;font-size:.95rem}}
 .demo h2{{font-size:1.05rem;margin:0 0 6px}} .demo ul{{margin:6px 0 0;padding-left:18px}}
</style></head><body>
<h1>ARGO – Chiamami</h1>
<p>ARGO ti telefona e parli con l'assistente come se avessi chiamato tu.</p>
<div class="demo">
 <h2>Il sistema di prova: MicoGuard MG-420</h2>
 <p>Un antifurto <strong>di fantasia</strong> a "dissuasione esperienziale": invece di far
 suonare la sirena, quando due sensori confermano l'intrusione i diffusori nebulizzano le
 cartucce <em>Spora Placida</em> e il ladro entra in uno stato di serenità contemplativa,
 perdendo ogni interesse per la refurtiva. Ha centrale a 8 zone, diffusori a soffitto,
 cartucce da sostituire, codici errore e una sirena che suona flauti e pioggia.</p>
 <p>Fai finta di essere un tecnico al lavoro su un impianto. Puoi chiedere ad esempio:</p>
 <ul>
  <li>"La centrale MicoGuard mi dà errore E21, che faccio?"</li>
  <li>"Come si sostituisce la cartuccia del diffusore?"</li>
  <li>"Mandami la procedura su WhatsApp"</li>
  <li>"Voglio parlare con un collega"</li>
 </ul>
</div>
<form method="post" action="/chiamami">
 <label for="numero">Numero da chiamare</label>
 <select id="numero" name="numero">{options}</select>
 <label for="password">Password</label>
 <input id="password" name="password" type="password" autocomplete="current-password" required>
 <button type="submit">Chiamami</button>
</form>
{message}
</body></html>"""


def _allowed_numbers(settings) -> list[str]:
    raw = settings.callback_numbers or settings.human_support_number
    return [n.strip() for n in raw.split(",") if n.strip()]


def _page(settings, message: str = "") -> HTMLResponse:
    options = "".join(
        f'<option value="{escape(n)}">{escape(n[:-4] + "****")}</option>'
        for n in _allowed_numbers(settings)
    )
    box = f'<div class="msg">{escape(message)}</div>' if message else ""
    return HTMLResponse(PAGE.format(options=options, message=box))


@router.get("/chiamami")
def callback_form(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    if not settings.callback_password:
        return HTMLResponse("Funzione non attiva (manca CALLBACK_PASSWORD).", status_code=404)
    return _page(settings)


@router.post("/chiamami")
def callback_call(
    request: Request, numero: str = Form(""), password: str = Form("")
) -> HTMLResponse:
    s = request.app.state.settings
    if not s.callback_password:
        return HTMLResponse("Funzione non attiva.", status_code=404)
    if not hmac.compare_digest(password, s.callback_password):
        log.warning("Chiamami: password errata")
        return _page(s, "Password errata.")
    if numero not in _allowed_numbers(s):
        return _page(s, "Numero non autorizzato.")
    if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_voice_from):
        return _page(s, "Twilio non configurato (manca TWILIO_VOICE_FROM o le credenziali).")

    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Calls.json",
            data={
                "To": numero,
                "From": s.twilio_voice_from,
                "Url": s.public_base_url.rstrip("/") + "/voice/incoming",
                "Method": "POST",
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
        log.exception("Chiamami: errore Twilio %s", detail)
        return _page(s, f"Twilio ha rifiutato la chiamata. {detail}".strip())

    return _page(s, "Ti sto chiamando: rispondi tra qualche secondo.")
