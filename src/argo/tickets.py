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


class JiraTicketService:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=15)

    def create(self, summary: str, description: str, reporter_contact: str) -> Ticket:
        s = self.settings
        body_text = f"{description}\n\nContatto tecnico: {reporter_contact}\nAperto da: ARGO"
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
        response = self.client.post(
            f"{s.jira_base_url.rstrip('/')}/rest/api/3/issue",
            json=payload,
            auth=(s.jira_email, s.jira_api_token),
        )
        response.raise_for_status()
        key = response.json()["key"]
        return Ticket(key=key, url=f"{s.jira_base_url.rstrip('/')}/browse/{key}")
