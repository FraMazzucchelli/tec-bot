from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"

    manuals_dir: str = "manuals"

    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "ASS"
    jira_issue_type: str = "Task"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    # Voce sintetica per le chiamate (Amazon Polly via Twilio).
    # Alternative italiane: Polly.Adriano-Neural, Polly.Bianca, Polly.Carla, Polly.Giorgio
    twilio_voice: str = "Polly.Bianca-Neural"

    # Numero dell'operatore umano in formato internazionale (es. +39...).
    # Non metterlo nel codice: il repo è pubblico, va solo nelle variabili d'ambiente.
    human_support_number: str = ""

    # Pagina /chiamami: ARGO telefona a uno dei numeri autorizzati
    twilio_voice_from: str = ""  # il numero Twilio, es. +16812562315
    callback_password: str = ""
    # numeri extra ammessi oltre ai cellulari italiani (es. esteri), separati da virgola
    callback_numbers: str = ""
    callback_max_per_hour: int = 20

    public_base_url: str = ""

    # Solo per prove in locale senza Twilio/Telegram: MAI true in produzione
    skip_signature_check: bool = False


    @field_validator("public_base_url")
    @classmethod
    def _normalize_url(cls, value: str) -> str:
        """Accetta anche 'dominio.app' o 'https://dominio.app/': la firma Twilio
        richiede esattamente https://dominio senza barra finale."""
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            value = "https://" + value
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
