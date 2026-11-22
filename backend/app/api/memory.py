from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.database.models import MemoryStatus
from app.memory.dependencies import (
    DbSessionDependency,
    MemoryActorDependency,
    MemoryExtractorDependency,
)
from app.memory.errors import (
    MemoryNotFoundError,
    MemoryPermissionError,
    MemoryProhibitedUseError,
    MemoryTransitionError,
)
from app.memory.household import (
    SensitiveValueError,
    checked_correction,
    sensitive_category,
    structured_kind,
    text_for_value,
)
from app.memory.household_service import (
    ForgetAllTokenError,
    HouseholdPreferenceService,
    MemoryPolicyError,
)
from app.memory.schemas import (
    ForgetAllConfirmationResponse,
    ForgetAllRequest,
    MemoryCorrection,
    MemoryDecision,
    MemoryExtractionRequest,
    MemoryExtractionResponse,
    MemoryPermissionResponse,
    MemoryPermissionUpsert,
    MemoryProposal,
    MemoryResponse,
    MemoryRetrieveRequest,
    MemoryRetrieveResponse,
)
from app.memory.prompts import MEMORY_EXTRACTION_PROMPT_VERSION
from app.memory.summary import MemorySummary, summarize_memory
from app.auth import UserIdDependency
from app.memory.service import MemoryService


router = APIRouter()
IdentifierPath = Annotated[
    str,
    Path(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$"),
]


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, MemoryPolicyError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if isinstance(exc, ForgetAllTokenError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if isinstance(exc, MemoryNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, MemoryTransitionError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, (MemoryPermissionError, MemoryProhibitedUseError)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    raise exc


@router.put(
    "/permissions/{data_category}/{purpose}",
    response_model=MemoryPermissionResponse,
)
async def set_permission(
    data_category: IdentifierPath,
    purpose: IdentifierPath,
    body: MemoryPermissionUpsert,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryPermissionResponse:
    try:
        permission = await MemoryService(session).set_permission(
            actor,
            data_category.lower(),
            purpose.lower(),
            body,
        )
        return MemoryPermissionResponse.model_validate(permission)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.get("/permissions", response_model=list[MemoryPermissionResponse])
async def list_permissions(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> list[MemoryPermissionResponse]:
    permissions = await MemoryService(session).list_permissions(actor)
    return [MemoryPermissionResponse.model_validate(item) for item in permissions]


@router.post("/forget-all/token", response_model=ForgetAllConfirmationResponse)
async def forget_all_confirmation(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> ForgetAllConfirmationResponse:
    confirmation = await HouseholdPreferenceService(session).forget_all_confirmation(actor)
    return ForgetAllConfirmationResponse(
        confirm_token=confirmation.token,
        count=confirmation.count,
        expires_at=confirmation.expires_at,
    )


@router.post("/forget-all", status_code=status.HTTP_204_NO_CONTENT)
async def forget_all(
    body: ForgetAllRequest,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    try:
        await HouseholdPreferenceService(session).forget_all_confirmed(actor, body.confirm_token)
    except ForgetAllTokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post(
    "/proposals",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def propose_memory(
    body: MemoryProposal,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryResponse:
    try:
        memory = await MemoryService(session).propose(actor, body)
        return MemoryResponse.model_validate(memory)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/extract", response_model=MemoryExtractionResponse)
async def extract_memory_proposals(
    body: MemoryExtractionRequest,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    extractor: MemoryExtractorDependency,
) -> MemoryExtractionResponse:
    try:
        extraction = await extractor.extract(body.message, body.locale)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Memory extraction is temporarily unavailable.",
        ) from exc

    service = MemoryService(session)
    proposals = []
    existing = await service.list_memories(actor, status=None, include_deleted=False)
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = {
        (
            memory.memory_type,
            tuple(sorted((str(key), str(value)) for key, value in memory.value.items())),
        )
        for memory in existing
        if memory.status in {MemoryStatus.PROPOSED, MemoryStatus.CONFIRMED}
    }
    for candidate in extraction.candidates:
        if candidate.evidence not in body.message:
            continue
        attributes = {item.key: item.value for item in candidate.attributes}
        fingerprint = (candidate.memory_type, tuple(sorted(attributes.items())))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        proposal = MemoryProposal(
            memory_type=candidate.memory_type,
            value=attributes,
            text=candidate.summary,
            source_type="explicit_statement",
            source_conversation_id=body.conversation_id,
            sensitivity="low",
            confidence=candidate.confidence,
            allowed_purposes=["personalize_search"],
        )
        proposals.append(await service.propose(actor, proposal))

    return MemoryExtractionResponse(
        extractor=extractor.name,
        prompt_version=MEMORY_EXTRACTION_PROMPT_VERSION,
        proposals=[MemoryResponse.model_validate(item) for item in proposals],
    )


@router.get("/summary", response_model=MemorySummary)
async def memory_summary(
    session: DbSessionDependency,
    user_id: UserIdDependency,
) -> MemorySummary:
    """'What do you know about me?': saved memories by group, each with its source."""
    await MemoryService(session).actor_for_external_id(user_id)
    return await summarize_memory(session, user_id)


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    session: DbSessionDependency,
    actor: MemoryActorDependency,
    memory_status: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    include_deleted: Annotated[bool, Query()] = False,
) -> list[MemoryResponse]:
    memories = await MemoryService(session).list_memories(
        actor, memory_status, include_deleted
    )
    return [MemoryResponse.model_validate(item) for item in memories]


@router.post("/retrieve", response_model=MemoryRetrieveResponse)
async def retrieve_memories(
    body: MemoryRetrieveRequest,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryRetrieveResponse:
    try:
        memories = await MemoryService(session).retrieve(actor, body)
        return MemoryRetrieveResponse(
            purpose=body.purpose,
            memories=[MemoryResponse.model_validate(item) for item in memories],
        )
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/{memory_id}/confirm", response_model=MemoryResponse)
async def confirm_memory(
    memory_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryResponse:
    try:
        memory = await MemoryService(session).confirm(actor, memory_id)
        return MemoryResponse.model_validate(memory)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/{memory_id}/reject", response_model=MemoryResponse)
async def reject_memory(
    memory_id: UUID,
    body: MemoryDecision,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryResponse:
    try:
        memory = await MemoryService(session).reject(actor, memory_id, body.reason)
        return MemoryResponse.model_validate(memory)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post(
    "/{memory_id}/correct",
    response_model=MemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def correct_memory(
    memory_id: UUID,
    body: MemoryCorrection,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryResponse:
    kind = structured_kind(body.memory_type, body.value)
    if kind is not None:
        try:
            cleaned = checked_correction(body.memory_type, body.value)
        except SensitiveValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, str(MemoryPolicyError(exc.category))
            ) from exc
        except ValueError as exc:
            message = exc.errors()[0].get("msg", str(exc)) if hasattr(exc, "errors") else str(exc)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message) from exc
        body = body.model_copy(update={"value": cleaned, "text": text_for_value(kind, cleaned)})
    else:
        category = sensitive_category(
            body.text, *(str(value) for value in body.value.values() if isinstance(value, str))
        )
        if category is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(MemoryPolicyError(category)))
    if body.value.get("origin") == "stated":
        # An on-screen edit is an explicit statement, not something learned in passing.
        body = body.model_copy(update={"value": {**body.value, "origin": "explicit"}})
    try:
        memory = await MemoryService(session).correct(actor, memory_id, body)
        return MemoryResponse.model_validate(memory)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/{memory_id}/undo", response_model=MemoryResponse | None)
async def undo_memory(
    memory_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> MemoryResponse | None:
    """Revert a memory learned in passing (origin stated) within 24 hours.

    Returns the memory it had replaced, now active again, or null.
    """
    try:
        restored = await HouseholdPreferenceService(session).undo(actor, memory_id)
        return MemoryResponse.model_validate(restored) if restored else None
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_memory(
    memory_id: UUID,
    session: DbSessionDependency,
    actor: MemoryActorDependency,
) -> None:
    try:
        await MemoryService(session).forget(actor, memory_id)
    except Exception as exc:
        _raise_http(exc)
