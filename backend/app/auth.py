from typing import Annotated

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings


PRIVATE_DEPLOYMENT_CODE = "private_deployment"
PRIVATE_DEPLOYMENT_MESSAGE = "This deployment is private."


class PrivateDeploymentForbidden(Exception):
    """A verified user who is not on this private deployment's allow-list."""

    code = PRIVATE_DEPLOYMENT_CODE
    message = PRIVATE_DEPLOYMENT_MESSAGE


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
    subject = subject.strip()
    allowed = settings.allowed_user_id_set
    if allowed and subject not in allowed:
        raise PrivateDeploymentForbidden()
    return subject


def require_owner_if_private(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Routes that are public locally still need the allow-listed owner when private."""

    if settings.private_deployment:
        require_user_id(request, settings)


def refuse_if_private(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """For unauthenticated browser navigations that have an authenticated replacement."""

    if settings.private_deployment:
        raise PrivateDeploymentForbidden()


UserIdDependency = Annotated[str, Depends(require_user_id)]
OwnerIfPrivateDependency = Depends(require_owner_if_private)
RefuseIfPrivateDependency = Depends(refuse_if_private)
