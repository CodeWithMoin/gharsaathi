from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PendingAuthorization(BaseModel):
    code_verifier: str = Field(repr=False)
    client_id: str
    created_at: datetime


class StoredConnection(BaseModel):
    client_id: str
    access_token: str | None = Field(default=None, repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: str = "Bearer"
    scope: str | None = None
    expires_at: datetime | None = None
    pending_authorizations: dict[str, PendingAuthorization] = Field(
        default_factory=dict,
        repr=False,
    )


class OAuthTokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str | None = Field(default=None, repr=False)
    token_type: Literal["Bearer", "bearer"] = "Bearer"
    expires_in: int = Field(gt=0)
    scope: str | None = None


class ConnectionStatus(BaseModel):
    connected: bool
    expires_at: datetime | None = None
    mode: Literal["demo", "live"] = "live"


class ToolDescription(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any]


class CapabilitySummary(BaseModel):
    """The Zepto functionality this application deliberately exposes.

    ``remote_tool_names`` is a diagnostic view of the authenticated MCP
    catalogue, not permission to invoke one of those tools.  In particular,
    this application exposes only read-only cart and order-history views. It
    has no Zepto cart mutation, payment, checkout, or order endpoint.
    """

    connected: bool
    catalogue_discovered: bool
    remote_tool_names: list[str] = Field(default_factory=list)
    exposed_actions: list[str] = Field(
        default_factory=lambda: ["connect", "disconnect", "inspect_catalogue"]
    )
    cart_actions_exposed: bool = False
    cart_read_exposed: bool = False
    order_history_exposed: bool = False
    checkout_exposed: bool = False
