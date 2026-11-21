from fastapi import APIRouter, HTTPException, status

from app.auth import UserIdDependency
from app.legal_consent import (
    LegalConsentStatus,
    LegalConsentSubmission,
    current_consent,
    record_consent,
    status_for,
)
from app.memory.dependencies import DbSessionDependency


router = APIRouter()


@router.get("", response_model=LegalConsentStatus)
async def get_legal_consent(
    session: DbSessionDependency,
    user_id: UserIdDependency,
) -> LegalConsentStatus:
    return status_for(await current_consent(session, user_id))


@router.post("", response_model=LegalConsentStatus, status_code=status.HTTP_201_CREATED)
async def accept_legal_consent(
    body: LegalConsentSubmission,
    session: DbSessionDependency,
    user_id: UserIdDependency,
) -> LegalConsentStatus:
    if not body.terms_accepted:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Terms and Privacy Policy acceptance is required.",
        )
    return status_for(await record_consent(session, user_id, body))
