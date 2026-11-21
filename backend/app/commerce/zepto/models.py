from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class StoredConnection(BaseModel):
    client_id: str
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: str = "Bearer"
    scope: str | None = None
    expires_at: datetime | None = None


class OAuthTokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: Literal["Bearer", "bearer"] = "Bearer"
    expires_in: int = Field(gt=0)
    scope: str | None = None


class ConnectionStatus(BaseModel):
    connected: bool
    expires_at: datetime | None = None


class ToolDescription(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any]
