import pytest
from fastapi import HTTPException, Request

from app.auth import require_user_id
from app.main import app


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Allow opt-in live provider smoke tests (also needs GHARSAATHI_LIVE_SMOKE=1).",
    )


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
