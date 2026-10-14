"""Apertura ticket su Jira Cloud (REST API v3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Settings


@dataclass
class Ticket:
    key: str
    url: str


class TicketService(Protocol):
    def create(self, summary: str, description: str, reporter_contact: str) -> Ticket: ...


class JiraError(Exception):
    """Errore restituito da Jira, con il motivo leggibile."""


def _jira_error(response: httpx.Response) -> str:
    hints = {
        401: "email o token API Jira non validi (JIRA_EMAIL, JIRA_API_TOKEN)",
        403: "l'utente Jira non ha il permesso di creare ticket in questo progetto",
        404: "indirizzo Jira o progetto non trovato (JIRA_BASE_URL, JIRA_PROJECT_KEY)",
    }
    detail = ""
    try:
        data = response.json()
        parts = list(data.get("errorMessages", [])) + [f"{k}: {v}" for k, v in data.get("errors", {}).items()]
        detail = "; ".join(parts)
    except ValueError:
        pass
    hint = hints.get(response.status_code, "")
    return f"Jira HTTP {response.status_code}" + (f": {detail}" if detail else "") + (f" ({hint})" if hint else "")


class JiraTicketService:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=15)

    @property
    def _base(self) -> str:
        return self.settings.jira_base_url.rstrip("/")

    @property
    def _auth(self) -> tuple[str, str]:
        return (self.settings.jira_email, self.settings.jira_api_token)

    def create(self, summary: str, description: str, reporter_contact: str) -> Ticket:
        s = self.settings
        body_text = f"{description}\n\nContatto tecnico: {reporter_contact}\nAperto da: {s.display_name}"
        payload = {
            "fields": {
                "project": {"key": s.jira_project_key},
                "issuetype": {"name": s.jira_issue_type},
                "summary": summary[:250],
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": para}]}
                        for para in body_text.split("\n")
                        if para.strip()
                    ],
                },
                "labels": ["argo"],
            }
        }
        response = self.client.post(f"{self._base}/rest/api/3/issue", json=payload, auth=self._auth)
        if response.status_code >= 400:
            raise JiraError(_jira_error(response))
        key = response.json()["key"]
        return Ticket(key=key, url=f"{self._base}/browse/{key}")

    def check(self) -> str:
        """Verifica accesso e progetto senza creare nulla. Restituisce 'ok' o il motivo."""
        try:
            me = self.client.get(f"{self._base}/rest/api/3/myself", auth=self._auth)
            if me.status_code >= 400:
                return _jira_error(me)
            project = self.client.get(
                f"{self._base}/rest/api/3/project/{self.settings.jira_project_key}", auth=self._auth
            )
            if project.status_code >= 400:
                return _jira_error(project)
            types = {t.get("name") for t in project.json().get("issueTypes", [])}
            if types and self.settings.jira_issue_type not in types:
                return (
                    f"tipo ticket '{self.settings.jira_issue_type}' non presente nel progetto "
                    f"{self.settings.jira_project_key} (disponibili: {', '.join(sorted(types))})"
                )
            return "ok"
        except httpx.HTTPError as exc:
            return f"Jira non raggiungibile: {type(exc).__name__}"
