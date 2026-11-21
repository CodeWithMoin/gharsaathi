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
from app.memory.schemas import (
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
from app.memory.service import MemoryService


router = APIRouter()
IdentifierPath = Annotated[
    str,
    Path(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$"),
]


def _raise_http(exc: Exception) -> None:
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
    try:
        memory = await MemoryService(session).correct(actor, memory_id, body)
        return MemoryResponse.model_validate(memory)
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
