import json
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database.models import (
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)


def _strip_identifier(value: str) -> str:
    stripped = value.strip().lower()
    if not stripped:
        raise ValueError("must not be blank")
    if not all(character.isalnum() or character in {"_", "-", "."} for character in stripped):
        raise ValueError("may contain only letters, numbers, dot, underscore, and hyphen")
    return stripped


class MemoryProposal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    memory_type: str = Field(min_length=2, max_length=80)
    value: dict[str, Any]
    text: str = Field(min_length=2, max_length=2000)
    source_type: MemorySourceType
    source_conversation_id: str | None = Field(default=None, max_length=128)
    sensitivity: MemorySensitivity = MemorySensitivity.LOW
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    allowed_purposes: list[str] = Field(min_length=1, max_length=20)
    valid_from: datetime | None = None
    expires_at: datetime | None = None

    @field_validator("memory_type")
    @classmethod
    def validate_memory_type(cls, value: str) -> str:
        return _strip_identifier(value)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 50:
            raise ValueError("may contain at most 50 fields")
        if len(json.dumps(value, separators=(",", ":"), default=str)) > 10_000:
            raise ValueError("serialized value must not exceed 10,000 characters")
        return value

    @field_validator("allowed_purposes")
    @classmethod
    def validate_purposes(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(_strip_identifier(value) for value in values))
        if not normalized:
            raise ValueError("must contain at least one purpose")
        return normalized

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.expires_at is not None and self.valid_from is not None:
            if self.expires_at <= self.valid_from:
                raise ValueError("expires_at must be after valid_from")
        return self


class MemoryCorrection(MemoryProposal):
    reason: str = Field(min_length=2, max_length=500)


class MemoryDecision(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class MemoryPermissionUpsert(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    allowed: bool
    granularity: str | None = Field(default=None, max_length=160)
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    sharing_scope: str = Field(default="agent_and_user", min_length=2, max_length=80)
    user_control: str = Field(default="view_edit_delete", min_length=2, max_length=80)


class MemoryPermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    data_category: str
    purpose: str
    allowed: bool
    granularity: str | None
    retention_days: int | None
    sharing_scope: str
    user_control: str
    confirmed_at: datetime
    revoked_at: datetime | None


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    memory_type: str
    value: dict[str, Any]
    text: str
    source_type: MemorySourceType
    source_conversation_id: str | None
    status: MemoryStatus
    sensitivity: MemorySensitivity
    confidence: float
    allowed_purposes: list[str]
    valid_from: datetime
    expires_at: datetime | None
    supersedes_memory_id: UUID | None
    confirmed_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryRetrieveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    purpose: str = Field(min_length=2, max_length=80)
    query: str | None = Field(default=None, max_length=300)
    memory_types: list[str] | None = Field(default=None, max_length=20)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        return _strip_identifier(value)

    @field_validator("memory_types")
    @classmethod
    def validate_memory_types(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(_strip_identifier(value) for value in values))


class MemoryRetrieveResponse(BaseModel):
    purpose: str
    memories: list[MemoryResponse]


MemoryCandidateType = Literal[
    "brand_preference",
    "pack_size_preference",
    "quantity_preference",
    "dietary_preference",
    "product_avoidance",
]


class MemoryAttribute(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    key: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    value: str = Field(min_length=1, max_length=160)


class ExtractedMemoryCandidate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    memory_type: MemoryCandidateType
    attributes: list[MemoryAttribute] = Field(min_length=1, max_length=5)
    summary: str = Field(min_length=2, max_length=300)
    evidence: str = Field(min_length=2, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)


class MemoryExtractionResult(BaseModel):
    candidates: list[ExtractedMemoryCandidate] = Field(max_length=5)


class MemoryExtractionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=2, max_length=4000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    locale: str = Field(default="en-IN", min_length=2, max_length=20)


class MemoryExtractionResponse(BaseModel):
    extractor: Literal["openai", "local_rules"]
    prompt_version: str
    proposals: list[MemoryResponse]
