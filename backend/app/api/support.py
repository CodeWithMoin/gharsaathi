from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.dependencies import SupportServiceDependency
from app.auth import UserIdDependency
from app.support.agent_tools import (
    AgentToolCall,
    AgentToolResult,
    execute_tool,
    tool_definitions,
)
from app.support.errors import (
    ComplaintNotFound,
    ComplaintStateError,
    EvidenceValidationError,
    SupportChannelUnavailable,
)
from app.support.models import ApproveComplaintRequest, Complaint, ComplaintCreate


router = APIRouter()


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ComplaintNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, EvidenceValidationError):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    if isinstance(exc, ComplaintStateError):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, SupportChannelUnavailable):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    raise exc


@router.get("/agent/tools", response_model=list[dict])
async def agent_tools() -> list[dict]:
    return tool_definitions()


@router.post("/agent/execute", response_model=AgentToolResult)
async def execute_agent_tool(
    body: AgentToolCall, service: SupportServiceDependency, user_id: UserIdDependency
) -> AgentToolResult:
    try:
        return execute_tool(service, user_id, body)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/complaints", response_model=Complaint, status_code=status.HTTP_201_CREATED)
async def create_complaint(
    body: ComplaintCreate, service: SupportServiceDependency, user_id: UserIdDependency
) -> Complaint:
    return service.create(user_id, body)


@router.get("/complaints/{complaint_id}", response_model=Complaint)
async def get_complaint(
    complaint_id: str, service: SupportServiceDependency, user_id: UserIdDependency
) -> Complaint:
    try:
        return service.get(user_id, complaint_id)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/complaints/{complaint_id}/evidence", response_model=Complaint)
async def add_evidence(
    complaint_id: str,
    service: SupportServiceDependency,
    user_id: UserIdDependency,
    evidence: Annotated[UploadFile, File(description="JPEG, PNG, or WebP; maximum 8 MB")],
) -> Complaint:
    try:
        return await service.add_evidence(user_id, complaint_id, evidence)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/complaints/{complaint_id}/prepare", response_model=Complaint)
async def prepare_complaint(
    complaint_id: str, service: SupportServiceDependency, user_id: UserIdDependency
) -> Complaint:
    try:
        return service.prepare(user_id, complaint_id)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/complaints/{complaint_id}/approve", response_model=Complaint)
async def approve_complaint(
    complaint_id: str,
    body: ApproveComplaintRequest,
    service: SupportServiceDependency,
    user_id: UserIdDependency,
) -> Complaint:
    try:
        return service.approve(user_id, complaint_id, body.expected_revision)
    except Exception as exc:
        _raise_http(exc)
        raise AssertionError("unreachable")


@router.post("/complaints/{complaint_id}/submit", status_code=status.HTTP_204_NO_CONTENT)
async def submit_complaint(
    complaint_id: str, service: SupportServiceDependency, user_id: UserIdDependency
) -> None:
    try:
        service.submit(user_id, complaint_id)
    except Exception as exc:
        _raise_http(exc)
