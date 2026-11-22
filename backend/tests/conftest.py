import os

# Tests never poll Sarvam: the shared .env may hold a real Voice Agents key.
os.environ["SARVAM_CALL_MONITOR_ENABLED"] = "false"

import re
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

from app.auth import require_user_id
from app.conversations.summarizer import DisabledConversationSummaries
from app.dependencies import get_conversation_summaries
from app.legal_consent import has_accepted_consent
from app.main import app
from tests.layers import LAYERS, LEGACY_LAYERS

TESTS_ROOT = Path(__file__).resolve().parent
FAILURE_MODES_DOC = TESTS_ROOT.parent.parent / "docs" / "FAILURE_MODES.md"


def failure_mode_test_refs() -> list[str]:
    """Backend test references (``tests/...py[::prefix]``) named in docs/FAILURE_MODES.md."""

    if not FAILURE_MODES_DOC.exists():
        return []
    return sorted(set(re.findall(r"`(tests/[\w/]+\.py(?:::\w+)?)`", FAILURE_MODES_DOC.read_text(encoding="utf-8"))))


def _layer_for(path: Path) -> str | None:
    relative = path.resolve().relative_to(TESTS_ROOT)
    if len(relative.parts) > 1 and relative.parts[0] in LAYERS:
        return relative.parts[0]
    return LEGACY_LAYERS.get(relative.as_posix())


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every test with exactly one layer before `-m` deselection runs."""

    unclassified: set[str] = set()
    refs = failure_mode_test_refs()
    for item in items:
        # `make test-failures` selects the tests the failure-mode catalogue names.
        if any(item.nodeid == ref or item.nodeid.startswith(ref + ("" if "::" in ref else "::")) for ref in refs):
            item.add_marker(pytest.mark.failure_mode)
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


@pytest.fixture(autouse=True)
def accepted_legal_consent():
    """Treat the test identity as having accepted the terms.

    Server-side consent enforcement is exercised by tests that request
    ``enforce_legal_consent``. Identity is still verified: the gate depends on
    ``require_user_id``.
    """

    app.dependency_overrides[has_accepted_consent] = lambda: True
    try:
        yield
    finally:
        app.dependency_overrides.pop(has_accepted_consent, None)


@pytest.fixture
def enforce_legal_consent(accepted_legal_consent):
    """Use the real, database-backed consent check for this test."""

    app.dependency_overrides.pop(has_accepted_consent, None)


@pytest.fixture(autouse=True)
def no_conversation_summariser():
    """Chat saves never reach OpenAI in tests; summariser tests install a fake."""

    app.dependency_overrides[get_conversation_summaries] = DisabledConversationSummaries
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_conversation_summaries, None)
