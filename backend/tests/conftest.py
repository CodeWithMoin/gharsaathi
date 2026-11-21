from pathlib import Path

import pytest
from fastapi import HTTPException, Request

from app.auth import require_user_id
from app.main import app
from tests.layers import LAYERS, LEGACY_LAYERS

TESTS_ROOT = Path(__file__).resolve().parent


def _layer_for(path: Path) -> str | None:
    relative = path.resolve().relative_to(TESTS_ROOT)
    if len(relative.parts) > 1 and relative.parts[0] in LAYERS:
        return relative.parts[0]
    return LEGACY_LAYERS.get(relative.as_posix())


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every test with exactly one layer before `-m` deselection runs."""

    unclassified: set[str] = set()
    for item in items:
        if any(item.iter_markers(name="live")) or any(item.iter_markers(name="live_cart")):
            continue
        layer = _layer_for(Path(item.fspath))
        if layer is None:
            unclassified.add(Path(item.fspath).name)
            continue
        if not any(item.iter_markers(name=layer)):
            item.add_marker(getattr(pytest.mark, layer))
    if unclassified:
        raise pytest.UsageError(
            "Unclassified test files: "
            f"{sorted(unclassified)}. Put new tests in tests/unit, tests/module or "
            "tests/integration, or add the file to LEGACY_LAYERS in tests/layers.py."
        )


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
