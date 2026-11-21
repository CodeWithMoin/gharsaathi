from typing import Annotated

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings


def require_user_id(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Return the verified Clerk subject; never trust a browser-supplied user ID."""

    secret_key = settings.clerk_secret_key.get_secret_value().strip()
    if not secret_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication is not configured.",
        )
    try:
        state = authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=secret_key,
                jwt_key=settings.clerk_jwt_key.strip() or None,
                authorized_parties=settings.clerk_authorized_parties,
                accepts_token=["session_token"],
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired session.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    subject = state.payload.get("sub") if state.is_signed_in and state.payload else None
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return subject.strip()


UserIdDependency = Annotated[str, Depends(require_user_id)]
