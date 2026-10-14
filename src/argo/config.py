from functools import lru_cache

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

    public_base_url: str = ""

    # Solo per prove in locale senza Twilio/Telegram: MAI true in produzione
    skip_signature_check: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
