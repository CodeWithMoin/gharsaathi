from pydantic import BaseModel, Field


class RealtimeClientSecret(BaseModel):
    client_secret: str = Field(repr=False)
    expires_at: int
    session_id: str
    model: str
    voice: str
    prompt_version: str
