"""Server web di ARGO: avvia con `uvicorn argo.main:app`."""

import logging
from pathlib import Path

from fastapi import FastAPI

from .assistant import Assistant
from .channels import app as app_channel
from .channels import callback, chat, relay, voice, web
from .config import Settings, get_settings
from .info import InfoAssistant, load_info
from .knowledge import KnowledgeBase
from .logging_privacy import install as install_logging
from .messaging import TwilioWhatsApp
from .tickets import JiraTicketService


def create_app(
    settings: Settings | None = None,
    assistant: Assistant | None = None,
    info_assistant: InfoAssistant | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    install_logging(logging.INFO)

    if assistant is None:
        knowledge = KnowledgeBase.from_dir(settings.manuals_dir)
        tickets = JiraTicketService(settings) if settings.jira_base_url else None
        messenger = (
            TwilioWhatsApp(settings)
            if settings.twilio_account_sid and settings.twilio_whatsapp_from
            else None
        )
        assistant = Assistant.from_settings(settings, knowledge, tickets, messenger)
    if info_assistant is None:
        # stesso client Claude e stesso Jira dell'assistente tecnico
        info_assistant = InfoAssistant(
            assistant.client, settings.claude_model, load_info(settings.info_file), assistant.tickets
        )

    app = FastAPI(title="ARGO - centralino tecnici")
    app.state.settings = settings
    app.state.assistant = assistant
    app.state.info_assistant = info_assistant
    app.include_router(voice.router)
    app.include_router(relay.router)
    app.include_router(chat.router)
    app.include_router(callback.router)
    app.include_router(web.router)
    app.include_router(app_channel.router)

    @app.get("/health")
    def health() -> dict:
        """Stato del servizio: solo sì/no, nessun segreto."""
        s = settings
        return {
            "status": "ok",
            "manuali": len(assistant.knowledge.passages),
            "config": {
                "claude": bool(s.anthropic_api_key),
                "url_pubblico": s.public_base_url or None,
                "twilio": bool(s.twilio_account_sid and s.twilio_auth_token),
                "whatsapp": assistant.messenger is not None,
                "whatsapp_mittente_ok": s.twilio_whatsapp_from.removeprefix("whatsapp:").startswith("+"),
                "ticket_jira": assistant.tickets is not None,
                "ticket_progetto": s.jira_project_key if assistant.tickets is not None else None,
                "operatore": bool(s.human_support_number),
                "voce": s.voice_mode,
                "app_voce": bool(s.twilio_api_key and s.twilio_api_secret and s.twilio_twiml_app_sid),
                "fatti_chiamare": bool(s.callback_enabled and s.twilio_voice_from),
                "codice_accesso": bool(s.callback_password),
                "chat_sito": s.web_chat_enabled,
                "info_commerciali": Path(s.info_file).is_file(),
            },
        }

    @app.get("/health/jira")
    def health_jira() -> dict:
        """Prova l'accesso a Jira senza creare ticket: 'ok' oppure il motivo dell'errore."""
        tickets = assistant.tickets
        if tickets is None or not hasattr(tickets, "check"):
            return {"jira": "non configurato (manca JIRA_BASE_URL)"}
        return {"jira": tickets.check()}

    return app


app = create_app()
