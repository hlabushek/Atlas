from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./atlas.db"
    owner_password: str = ""
    session_secret: str = ""
    cookie_secure: bool = False
    model_provider: str = "demo"
    model_url: str = "http://localhost:11434/v1"
    model_name: str = ""
    model_api_key: str = ""
    routerai_api_key: str = ""
    plusvibe_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_owner_id: int | None = None
    telegram_proxy: str = ""
    telegram_stt_model: str = "assemblyai/universal-3-5-pro"
    telegram_stt_kopecks_per_minute: int = Field(default=41, ge=1, le=100000)
    integration_hosts: str = ""
    embedding_url: str = ''
    embedding_model: str = ''
    embedding_api_key: str = ''
    vapid_private_key: str = ''
    vapid_public_key: str = ''
    vapid_subject: str = ''
    push_hosts: str = 'fcm.googleapis.com,updates.push.services.mozilla.com,web.push.apple.com'
    max_output_tokens: int = Field(default=1024, ge=32, le=8192)
    @model_validator(mode="after")
    def validate_secrets(self):
        if len(self.owner_password) < 16 or len(self.session_secret) < 32:
            raise ValueError("Configure owner password (16+ chars) and session secret (32+ chars)")
        if self.model_provider not in {"demo", "ollama", "openai-compatible"}:
            raise ValueError("Unknown model provider")
        return self

@lru_cache
def settings():
    return Settings()
