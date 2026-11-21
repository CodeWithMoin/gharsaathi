import pytest
from fastapi import HTTPException, Request

from app.auth import require_user_id
from app.main import app


@pytest.fixture(autouse=True)
def verified_test_identity():
    """Unit/API tests bypass Clerk with an explicit dependency override only."""

    def identity(request: Request) -> str:
        value = request.headers.get("X-Test-User-Id", "test-user").strip()
        if not value:
            raise HTTPException(422, "Invalid test identity.")
        return value

    app.dependency_overrides[require_user_id] = identity
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_user_id, None)
