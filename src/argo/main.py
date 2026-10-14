"""Server web di ARGO: avvia con `uvicorn argo.main:app`."""

import logging

from fastapi import FastAPI

from .assistant import Assistant
from .channels import callback, chat, voice
from .config import Settings, get_settings
from .knowledge import KnowledgeBase
from .messaging import TwilioWhatsApp
from .tickets import JiraTicketService


def create_app(settings: Settings | None = None, assistant: Assistant | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    if assistant is None:
        knowledge = KnowledgeBase.from_dir(settings.manuals_dir)
        tickets = JiraTicketService(settings) if settings.jira_base_url else None
        messenger = (
            TwilioWhatsApp(settings)
            if settings.twilio_account_sid and settings.twilio_whatsapp_from
            else None
        )
        assistant = Assistant.from_settings(settings, knowledge, tickets, messenger)

    app = FastAPI(title="ARGO - centralino tecnici")
    app.state.settings = settings
    app.state.assistant = assistant
    app.include_router(voice.router)
    app.include_router(chat.router)
    app.include_router(callback.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "manuali": len(assistant.knowledge.passages)}

    return app


app = create_app()
