from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    swiggy_base_url: str = "https://mcp.swiggy.com"
    swiggy_mcp_url: str = "https://mcp.swiggy.com/im"
    swiggy_oauth_redirect_uri: str = (
        "http://localhost:8000/api/swiggy/oauth/callback"
    )
    zepto_authorization_server_url: str = "https://auth.zepto.co.in"
    zepto_resource_url: str = "https://mcp.zepto.co.in"
    zepto_mcp_url: str = "https://mcp.zepto.co.in/ucp/mcp"
    zepto_oauth_redirect_uri: str = "http://localhost/callback"
    frontend_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"
    app_encryption_key: str = ""
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/equal_ai"
    )
    openai_api_key: str = Field(default="", repr=False)
    memory_extraction_model: str = "gpt-4o-mini"
    swiggy_rate_limit_fallback_seconds: int = Field(default=30, ge=1, le=300)
    swiggy_retry_budget_seconds: int = Field(default=30, ge=1, le=120)
    swiggy_tool_schema_cache_seconds: int = Field(default=300, ge=0, le=3600)
    swiggy_token_store_path: Path = Field(
        default=Path("backend/.data/swiggy_connection.enc")
    )
    zepto_token_store_path: Path = Field(
        default=Path("backend/.data/zepto_connection.enc")
    )

    @field_validator(
        "swiggy_token_store_path", "zepto_token_store_path", mode="after"
    )
    @classmethod
    def make_store_path_absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else REPOSITORY_ROOT / value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
