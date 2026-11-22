from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    swiggy_base_url: str = "https://mcp.swiggy.com"
    commerce_mode: Literal["demo", "live"] = "demo"
    swiggy_mcp_url: str = "https://mcp.swiggy.com/im"
    swiggy_oauth_redirect_uri: str = (
        "http://localhost:8000/api/swiggy/oauth/callback"
    )
    zepto_authorization_server_url: str = "https://auth.zepto.co.in"
    zepto_resource_url: str = "https://mcp.zepto.co.in"
    zepto_mcp_url: str = "https://mcp.zepto.co.in/mcp"
    zepto_oauth_redirect_uri: str = "http://localhost/callback"
    zepto_oauth_scope: str = "tools:read"
    # Prepaid Zepto orders (UPI, Zepto Cash, pay online). Off by default; even
    # when on, a method is offered only if it is allow-listed here, covered by
    # the verified response contract (app/commerce/zepto/payments.py) and listed
    # by Zepto's get_payment_methods. Cash on delivery is always offered.
    zepto_online_payments_enabled: bool = False
    zepto_payment_methods_allowed: str = "upi_reserve,wallet,online"
    frontend_url: str = "http://localhost:3003"
    cors_origins: str = "http://localhost:3003,http://127.0.0.1:3003"
    app_encryption_key: str = ""
    clerk_secret_key: SecretStr = SecretStr("")
    clerk_jwt_key: str = ""
    clerk_authorized_parties_csv: str = ""
    local_encryption_key_path: Path = Field(
        default=Path("backend/.data/app_encryption.key")
    )
    database_url: str = ""
    database_host: str = ""
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "equal_ai"
    database_user: str = "postgres"
    database_password: SecretStr = SecretStr("postgres")
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    live_model: str = "gpt-live-1.1"
    live_voice: str = "nira"
    live_backend_model: str = "gpt-6-luna"
    # Typed chat runs the same backend model and tools as voice, one bounded turn per request.
    chat_turn_timeout_seconds: float = Field(default=60.0, ge=5.0, le=180.0)
    # Shop from a photo: vision model for reading attached photos (Responses API, image input).
    photo_extraction_model: str = "gpt-6-luna"
    # native | tuned (option A) | smart_turn (option B); see docs/voice-turn-taking.md
    voice_turn_taking: Literal["native", "tuned", "smart_turn"] = "native"
    # Dev diagnostic: log content-free sideband event summaries (type, id field names, timing).
    voice_event_log: bool = False
    memory_extraction_model: str = "gpt-4o-mini"
    # Background rolling chat summaries (app/conversations/summarizer.py).
    conversation_summary_model: str = "gpt-6-luna"
    jev_api_key: SecretStr = SecretStr("")
    jev_base_url: str = "https://api.typesafe.ai/v1"
    jev_model: str = "jev-1.14.0"
    jev_timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)
    jev_reranking_enabled: bool = False
    swiggy_rate_limit_fallback_seconds: int = Field(default=30, ge=1, le=300)
    swiggy_token_store_path: Path = Field(
        default=Path("backend/.data/swiggy_connection.enc")
    )
    zepto_token_store_path: Path = Field(
        default=Path("backend/.data/zepto_connection.enc")
    )
    support_data_path: Path = Field(default=Path("backend/.data/support"))
    tracking_data_path: Path = Field(default=Path("backend/.data/tracking"))
    tracking_polling_enabled: bool = True

    @field_validator(
        "swiggy_token_store_path",
        "zepto_token_store_path",
        "local_encryption_key_path",
        "support_data_path",
        "tracking_data_path",
        mode="after",
    )
    @classmethod
    def make_store_path_absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else REPOSITORY_ROOT / value

    @model_validator(mode="after")
    def assemble_database_url(self) -> "Settings":
        if self.database_url:
            return self
        host = self.database_host or "localhost"
        user = quote_plus(self.database_user)
        password = quote_plus(self.database_password.get_secret_value())
        name = quote_plus(self.database_name)
        self.database_url = (
            f"postgresql+asyncpg://{user}:{password}@{host}:{self.database_port}/{name}"
        )
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def clerk_authorized_parties(self) -> list[str]:
        configured = [
            party.strip()
            for party in self.clerk_authorized_parties_csv.split(",")
            if party.strip()
        ]
        return configured or self.allowed_origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
