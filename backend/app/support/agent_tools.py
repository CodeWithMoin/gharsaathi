from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.support.errors import ComplaintStateError
from app.support.models import (
    Complaint,
    ComplaintCreate,
)
from app.support.service import SupportService


class GetComplaintArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complaint_id: str = Field(min_length=36, max_length=36)


class ApproveComplaintArguments(GetComplaintArguments):
    expected_revision: int = Field(ge=1)


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "report_order_issue",
        "get_support_complaint",
        "prepare_support_complaint",
        "approve_support_complaint",
    ]
    arguments: dict[str, Any]


class AgentToolResult(BaseModel):
    complaint: Complaint
    requires_user_approval: bool
    submitted: bool = False
    guidance: str


def tool_definitions() -> list[dict[str, Any]]:
    """Function definitions suitable for GPT Live/Realtime client-side tools."""
    return [
        {
            "type": "function",
            "name": "report_order_issue",
            "description": (
                "Create and prepare a customer-support complaint after gathering every "
                "required fact conversationally. Use only an order ID verified through "
                "order history or stated by the user; never invent one. This prepares a "
                "draft and never submits it."
            ),
            "strict": True,
            "parameters": ComplaintCreate.model_json_schema(),
        },
        {
            "type": "function",
            "name": "get_support_complaint",
            "description": (
                "Retrieve an existing complaint so its exact current version can be "
                "read back before approval."
            ),
            "strict": True,
            "parameters": GetComplaintArguments.model_json_schema(),
        },
        {
            "type": "function",
            "name": "prepare_support_complaint",
            "description": (
                "Regenerate the exact complaint summary after evidence or other case "
                "data changed. The resulting version must be read back and explicitly "
                "approved before any future submission."
            ),
            "strict": True,
            "parameters": GetComplaintArguments.model_json_schema(),
        },
        {
            "type": "function",
            "name": "approve_support_complaint",
            "description": (
                "Record approval for the exact prepared complaint version. Call only "
                "after the user explicitly approves the summary. Approval does not "
                "submit the complaint or approve a refund/replacement."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "complaint_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                },
                "required": ["complaint_id", "expected_revision"],
                "additionalProperties": False,
            },
        },
    ]


def execute_tool(service: SupportService, user_id: str, call: AgentToolCall) -> AgentToolResult:
    try:
        if call.name == "report_order_issue":
            created = service.create(user_id, ComplaintCreate.model_validate(call.arguments))
            complaint = service.prepare(user_id, created.id)
            return AgentToolResult(
                complaint=complaint,
                requires_user_approval=True,
                guidance=(
                    "Read back the exact prepared summary and ask for explicit approval. "
                    "Do not say it was submitted."
                ),
            )
        if call.name == "get_support_complaint":
            arguments = GetComplaintArguments.model_validate(call.arguments)
            complaint = service.get(user_id, arguments.complaint_id)
            return AgentToolResult(
                complaint=complaint,
                requires_user_approval=complaint.approval is None,
                guidance="Use the stored facts; do not add or infer complaint details.",
            )
        if call.name == "prepare_support_complaint":
            arguments = GetComplaintArguments.model_validate(call.arguments)
            complaint = service.prepare(user_id, arguments.complaint_id)
            return AgentToolResult(
                complaint=complaint,
                requires_user_approval=True,
                guidance=(
                    "Read back the exact updated summary and ask for explicit approval."
                ),
            )

        approval = ApproveComplaintArguments.model_validate(call.arguments)
        complaint = service.approve(
            user_id, approval.complaint_id, approval.expected_revision
        )
        return AgentToolResult(
            complaint=complaint,
            requires_user_approval=False,
            guidance=(
                "Approval is recorded, but no authorised submission channel is "
                "configured. Tell the user it has not been sent."
            ),
        )
    except ValidationError as exc:
        raise ComplaintStateError(
            "The support tool arguments were incomplete or invalid. Ask only for the "
            "missing information."
        ) from exc
