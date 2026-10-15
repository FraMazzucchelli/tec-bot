"""App di ARGO (PWA): il tecnico parla con ARGO dal telefono, via internet.

La chiamata parte dal browser con il Twilio Voice SDK e arriva a Twilio come una
normale telefonata verso la TwiML App, che punta a /voice/incoming: da lì in poi
il flusso è lo stesso del numero di telefono (Gather o ConversationRelay, ricerca
nei manuali, ticket, passaggio a un collega).

Configurazione su Twilio (una volta sola):
- Account → API keys & tokens → crea una API key "Standard":
  TWILIO_API_KEY (SK…) e TWILIO_API_SECRET;
- Voice → TwiML Apps → crea un'app con Voice URL `{PUBLIC_BASE_URL}/voice/incoming`
  (POST): TWILIO_TWIML_APP_SID (AP…).

L'endpoint /app/token è pubblico e ogni chiamata costa: se è impostato
CALLBACK_PASSWORD serve lo stesso codice di accesso, e i token sono limitati a
APP_MAX_CALLS_PER_HOUR all'ora.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

BASE = Path(__file__).resolve().parent.parent
TEMPLATE = (BASE / "templates" / "app.html").read_text(encoding="utf-8")
STATIC = BASE / "static"
TOKEN_TTL_SECONDS = 600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def access_token(
    account_sid: str,
    api_key: str,
    api_secret: str,
    identity: str,
    app_sid: str,
    ttl: int = TOKEN_TTL_SECONDS,
    now: int | None = None,
) -> str:
    """Access Token Twilio (JWT HS256) con il permesso di chiamare la TwiML App.

    Stesso formato della libreria ufficiale `twilio.jwt.access_token`, senza
    aggiungere la dipendenza.
    """
    now = int(time.time()) if now is None else now
    header = {"typ": "JWT", "alg": "HS256", "cty": "twilio-fpa;v=1"}
    payload = {
        "jti": f"{api_key}-{now}",
        "iss": api_key,
        "sub": account_sid,
        "nbf": now,
        "exp": now + ttl,
        "grants": {
            "identity": identity,
            "voice": {"outgoing": {"application_sid": app_sid}, "incoming": {"allow": False}},
        },
    }
    signing = (
        _b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64(json.dumps(payload, separators=(",", ":")).encode())
    )
    signature = hmac.new(api_secret.encode(), signing.encode(), hashlib.sha256).digest()
    return signing + "." + _b64(signature)


APP_PHONE_TTL_SECONDS = 2 * 3600
INTERNATIONAL_NUMBER = re.compile(r"^\+[1-9]\d{7,14}$")


def remember_phone(state, call_sid: str, raw: str, settings) -> None:
    """Chiamata dall'app con il cellulare indicato dal tecnico: lo teniamo in memoria
    per la durata della chiamata, così ARGO può mandargli le schede su WhatsApp.
    Qualsiasi numero in formato internazionale valido: i messaggi WhatsApp non
    costano come le chiamate, quindi non serve la restrizione di "Fatti chiamare"."""
    from .callback import normalize_number

    if not call_sid or not raw.strip():
        return
    number = normalize_number(raw)
    if not INTERNATIONAL_NUMBER.match(number):
        log.warning("App: cellulare non valido, ignorato")
        return
    phones = getattr(state, "app_phones", None)
    if phones is None:
        phones = state.app_phones = {}
    now = time.time()
    for sid in [s for s, (_, t) in phones.items() if now - t > APP_PHONE_TTL_SECONDS]:
        phones.pop(sid, None)
    phones[call_sid] = (number, now)


def phone_for_call(state, call_sid: str, fallback: str) -> str:
    """Il cellulare indicato nell'app per questa chiamata, altrimenti `fallback`."""
    entry = getattr(state, "app_phones", {}).get(call_sid)
    return entry[0] if entry else fallback


class TokenLimiter:
    """Token rilasciati nell'ultima ora (in memoria, per un solo server)."""

    def __init__(self, max_per_hour: int):
        self.max_per_hour = max_per_hour
        self.times: list[float] = []

    def allow(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self.times = [t for t in self.times if now - t < 3600]
        if self.max_per_hour and len(self.times) >= self.max_per_hour:
            return False
        self.times.append(now)
        return True


class TokenRequest(BaseModel):
    codice: str = ""


def _configured(s) -> bool:
    return bool(s.twilio_account_sid and s.twilio_api_key and s.twilio_api_secret and s.twilio_twiml_app_sid)


@router.get("/app", response_class=HTMLResponse)
def app_page(request: Request) -> HTMLResponse:
    s = request.app.state.settings
    html = TEMPLATE.replace("{{SERVE_CODICE}}", "true" if s.callback_password else "false").replace(
        "{{NOME}}", s.display_name
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@router.get("/app/", include_in_schema=False)
def app_slash(request: Request) -> HTMLResponse:
    return app_page(request)


@router.post("/app/token")
def app_token(body: TokenRequest, request: Request) -> JSONResponse:
    state = request.app.state
    s = state.settings
    if not s.app_enabled:
        return JSONResponse({"errore": "L'app è disattivata."}, status_code=403)
    if s.callback_password and not hmac.compare_digest(body.codice.encode(), s.callback_password.encode()):
        return JSONResponse({"errore": "Codice di accesso non corretto.", "codice": "codice"}, status_code=403)
    if not _configured(s):
        log.warning("App: mancano TWILIO_API_KEY, TWILIO_API_SECRET o TWILIO_TWIML_APP_SID")
        return JSONResponse({"errore": "La voce nell'app non è ancora configurata sul server."}, status_code=503)
    if not hasattr(state, "app_limiter"):
        state.app_limiter = TokenLimiter(s.app_max_calls_per_hour)
    if not state.app_limiter.allow():
        return JSONResponse({"errore": "Troppe chiamate nell'ultima ora: riprova più tardi."}, status_code=429)

    identity = "argo-app-" + secrets.token_hex(6)
    token = access_token(
        s.twilio_account_sid, s.twilio_api_key, s.twilio_api_secret, identity, s.twilio_twiml_app_sid
    )
    return JSONResponse({"token": token, "identita": identity, "scadenza": TOKEN_TTL_SECONDS})


MANIFEST = {
    "name": "ARGO – assistenza tecnica",
    "short_name": "ARGO",
    "description": "Parla con ARGO: risponde dai manuali, apre i ticket e ti passa un collega.",
    "start_url": "/app",
    "scope": "/app",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#1D2226",
    "theme_color": "#1D2226",
    "lang": "it",
    "icons": [
        {"src": "/app/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/app/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}


@router.get("/app/manifest.webmanifest")
def manifest() -> Response:
    return Response(json.dumps(MANIFEST, ensure_ascii=False), media_type="application/manifest+json")


@router.get("/app/icon-{size}.png")
def icon(size: int) -> Response:
    if size not in (192, 512):
        return Response(status_code=404)
    return FileResponse(STATIC / f"icon-{size}.png", media_type="image/png")


# Service worker minimo: serve per installare l'app e per mostrare la pagina anche
# senza rete (la chiamata ovviamente richiede la connessione). Non mette mai in
# cache i token.
SERVICE_WORKER = """const CACHE = "argo-app-v1";
const SHELL = ["/app", "/app/manifest.webmanifest", "/app/icon-192.png", "/app/icon-512.png"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || !SHELL.includes(url.pathname)) return;
  // prima la rete (versione aggiornata), poi la copia salvata
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});
"""


@router.get("/app/sw.js")
def service_worker() -> Response:
    return Response(
        SERVICE_WORKER,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/app"},
    )
