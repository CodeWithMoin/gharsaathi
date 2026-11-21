import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from app.support.errors import ComplaintNotFound, EvidenceValidationError
from app.support.models import Complaint, Evidence


MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
    "image/webp": (b"RIFF", ".webp"),
}


class ComplaintStore:
    def __init__(self, root: Path):
        self.root = root
        self.complaints_path = root / "complaints"
        self.evidence_path = root / "evidence"

    @staticmethod
    def _validated_id(complaint_id: str) -> str:
        try:
            return str(UUID(complaint_id))
        except ValueError as exc:
            raise ComplaintNotFound("Complaint was not found.") from exc

    def _path_for(self, complaint_id: str) -> Path:
        return self.complaints_path / f"{self._validated_id(complaint_id)}.json"

    def load(self, complaint_id: str) -> Complaint:
        path = self._path_for(complaint_id)
        try:
            return Complaint.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ComplaintNotFound("Complaint was not found.") from exc

    def save(self, complaint: Complaint) -> Complaint:
        self.complaints_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path_for(complaint.id)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(complaint.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return complaint

    def save_evidence(
        self,
        complaint_id: str,
        original_name: str | None,
        declared_content_type: str | None,
        data: bytes,
        created_at,
    ) -> Evidence:
        self._validated_id(complaint_id)
        if not data or len(data) > MAX_EVIDENCE_BYTES:
            raise EvidenceValidationError("Evidence must be an image no larger than 8 MB.")
        detected_type, extension = self._detect_image(data)
        if declared_content_type not in ALLOWED_IMAGE_TYPES:
            raise EvidenceValidationError("Only JPEG, PNG, and WebP evidence is supported.")
        if declared_content_type != detected_type:
            raise EvidenceValidationError("The uploaded file contents do not match its image type.")

        evidence_id = str(uuid4())
        directory = self.evidence_path / complaint_id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = directory / f"{evidence_id}{extension}"
        destination.write_bytes(data)
        os.chmod(destination, 0o600)
        return Evidence(
            id=evidence_id,
            file_name=self._safe_display_name(original_name, extension),
            content_type=detected_type,
            size_bytes=len(data),
            created_at=created_at,
        )

    @staticmethod
    def _detect_image(data: bytes) -> tuple[str, str]:
        for content_type, (signature, extension) in ALLOWED_IMAGE_TYPES.items():
            if data.startswith(signature):
                if content_type == "image/webp" and data[8:12] != b"WEBP":
                    continue
                return content_type, extension
        raise EvidenceValidationError("The uploaded file is not a supported image.")

    @staticmethod
    def _safe_display_name(original_name: str | None, extension: str) -> str:
        name = Path(original_name or f"evidence{extension}").name
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
        return (name or f"evidence{extension}")[:120]
