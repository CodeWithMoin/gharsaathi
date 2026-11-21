import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import UploadFile

from app.support.errors import ComplaintStateError, SupportChannelUnavailable
from app.support.models import (
    Complaint,
    ComplaintApproval,
    ComplaintCreate,
    ComplaintStatus,
)
from app.support.store import MAX_EVIDENCE_BYTES, ComplaintStore


class SupportService:
    def __init__(self, store: ComplaintStore):
        self.store = store

    def create(self, request: ComplaintCreate) -> Complaint:
        now = datetime.now(UTC)
        complaint = Complaint(
            id=str(uuid4()),
            **request.model_dump(),
            status=ComplaintStatus.ISSUE_REPORTED,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        return self.store.save(complaint)

    def get(self, complaint_id: str) -> Complaint:
        return self.store.load(complaint_id)

    async def add_evidence(
        self, complaint_id: str, upload: UploadFile
    ) -> Complaint:
        complaint = self.store.load(complaint_id)
        data = await upload.read(MAX_EVIDENCE_BYTES + 1)
        evidence = self.store.save_evidence(
            complaint.id,
            upload.filename,
            upload.content_type,
            data,
            datetime.now(UTC),
        )
        complaint.evidence.append(evidence)
        self._invalidate_approval(complaint)
        complaint.status = ComplaintStatus.COLLECTING_EVIDENCE
        complaint.revision += 1
        complaint.updated_at = datetime.now(UTC)
        return self.store.save(complaint)

    def prepare(self, complaint_id: str) -> Complaint:
        complaint = self.store.load(complaint_id)
        complaint.prepared_summary = self._summary(complaint)
        complaint.approval = None
        complaint.status = ComplaintStatus.AWAITING_APPROVAL
        complaint.revision += 1
        complaint.updated_at = datetime.now(UTC)
        return self.store.save(complaint)

    def approve(self, complaint_id: str, expected_revision: int) -> Complaint:
        complaint = self.store.load(complaint_id)
        if complaint.status != ComplaintStatus.AWAITING_APPROVAL:
            raise ComplaintStateError("Prepare the complaint before approving it.")
        if complaint.revision != expected_revision:
            raise ComplaintStateError(
                "The complaint changed after it was reviewed. Review the latest version."
            )
        complaint.approval = ComplaintApproval(
            revision=complaint.revision,
            complaint_digest=self._digest(complaint),
            approved_at=datetime.now(UTC),
        )
        complaint.status = ComplaintStatus.APPROVED_PENDING_SUBMISSION
        complaint.updated_at = datetime.now(UTC)
        return self.store.save(complaint)

    def submit(self, complaint_id: str) -> None:
        complaint = self.store.load(complaint_id)
        if complaint.approval is None:
            raise ComplaintStateError("Approve the exact complaint before submission.")
        raise SupportChannelUnavailable(complaint.submission.reason)

    @staticmethod
    def _invalidate_approval(complaint: Complaint) -> None:
        complaint.approval = None
        complaint.prepared_summary = None

    @staticmethod
    def _summary(complaint: Complaint) -> str:
        evidence = (
            ", ".join(item.file_name for item in complaint.evidence)
            if complaint.evidence
            else "None attached"
        )
        return "\n".join(
            [
                f"Provider: {complaint.provider.value.title()}",
                f"Order ID: {complaint.order_id}",
                f"Affected item: {complaint.affected_product}",
                f"Issue category: {complaint.category.value.replace('_', ' ')}",
                f"Customer description: {complaint.description}",
                f"Requested resolution: {complaint.desired_resolution.value.replace('_', ' ')}",
                f"User-provided evidence: {evidence}",
                "Evidence is attached for provider review and is not independently verified.",
            ]
        )

    @staticmethod
    def _digest(complaint: Complaint) -> str:
        exact_payload = {
            "provider": complaint.provider,
            "order_id": complaint.order_id,
            "affected_product": complaint.affected_product,
            "category": complaint.category,
            "description": complaint.description,
            "desired_resolution": complaint.desired_resolution,
            "evidence": [item.id for item in complaint.evidence],
            "prepared_summary": complaint.prepared_summary,
            "revision": complaint.revision,
        }
        encoded = json.dumps(exact_payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
