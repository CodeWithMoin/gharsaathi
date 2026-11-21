from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommerceProvider(StrEnum):
    SWIGGY = "swiggy"
    ZEPTO = "zepto"
    OTHER = "other"


class IssueCategory(StrEnum):
    SPOILED_PRODUCT = "spoiled_product"
    DAMAGED_PRODUCT = "damaged_product"
    MISSING_PRODUCT = "missing_product"
    INCORRECT_PRODUCT = "incorrect_product"
    INCORRECT_QUANTITY = "incorrect_quantity"
    DELAYED_DELIVERY = "delayed_delivery"
    OTHER = "other"


class DesiredResolution(StrEnum):
    REFUND = "refund"
    REPLACEMENT = "replacement"
    PROVIDER_SUPPORT = "provider_support"


class ComplaintStatus(StrEnum):
    ISSUE_REPORTED = "issue_reported"
    COLLECTING_EVIDENCE = "collecting_evidence"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED_PENDING_SUBMISSION = "approved_pending_submission"
    SUBMITTED = "submitted"
    AWAITING_RESOLUTION = "awaiting_resolution"
    RESOLVED = "resolved"
    FAILED = "failed"


class Evidence(BaseModel):
    id: str
    file_name: str
    content_type: str
    size_bytes: int = Field(ge=1)
    created_at: datetime
    source: str = "user_provided"


class ComplaintApproval(BaseModel):
    revision: int = Field(ge=1)
    complaint_digest: str
    approved_at: datetime


class SubmissionCapability(BaseModel):
    available: bool = False
    reason: str = (
        "No authorised provider complaint-submission channel is configured. "
        "The complaint can be reviewed and copied, but has not been sent."
    )


class ComplaintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: CommerceProvider
    order_id: str = Field(min_length=2, max_length=128)
    affected_product: str = Field(min_length=2, max_length=200)
    category: IssueCategory
    description: str = Field(min_length=5, max_length=2000)
    desired_resolution: DesiredResolution

    @field_validator("order_id", "affected_product", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class Complaint(BaseModel):
    id: str
    user_id: str = Field(min_length=1, max_length=128, exclude=True)
    provider: CommerceProvider
    order_id: str
    affected_product: str
    category: IssueCategory
    description: str
    desired_resolution: DesiredResolution
    status: ComplaintStatus
    revision: int = Field(ge=1)
    evidence: list[Evidence] = Field(default_factory=list)
    prepared_summary: str | None = None
    approval: ComplaintApproval | None = None
    submission: SubmissionCapability = Field(default_factory=SubmissionCapability)
    created_at: datetime
    updated_at: datetime


class ApproveComplaintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
