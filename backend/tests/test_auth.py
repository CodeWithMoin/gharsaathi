from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth import require_user_id
from app.core.config import Settings


def request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers or []})


def test_auth_fails_closed_when_clerk_is_not_configured(monkeypatch, tmp_path) -> None:
    # No root key and no frontend/.env.local to fall back to.
    monkeypatch.setattr("app.core.config.REPOSITORY_ROOT", tmp_path)
    with pytest.raises(HTTPException) as caught:
        require_user_id(request(), Settings(_env_file=None, clerk_secret_key=""))
    assert caught.value.status_code == 503


def test_auth_uses_verified_clerk_subject_not_spoofed_header(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.auth.authenticate_request",
        lambda *_: SimpleNamespace(is_signed_in=True, payload={"sub": "user_verified"}),
    )
    actor = require_user_id(
        request([(b"x-user-id", b"attacker")]),
        Settings(_env_file=None, clerk_secret_key="sk_test_fake"),
    )
    assert actor == "user_verified"


def test_auth_rejects_unsigned_session(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.auth.authenticate_request",
        lambda *_: SimpleNamespace(is_signed_in=False, payload={}),
    )
    with pytest.raises(HTTPException) as caught:
        require_user_id(
            request([(b"authorization", b"Bearer invalid")]),
            Settings(_env_file=None, clerk_secret_key="sk_test_fake"),
        )
    assert caught.value.status_code == 401


def test_backend_falls_back_to_the_frontend_clerk_secret(monkeypatch, tmp_path) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / ".env.local").write_text("CLERK_SECRET_KEY=sk_test_fallback\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config.REPOSITORY_ROOT", tmp_path)
    assert Settings(_env_file=None, clerk_secret_key="").clerk_secret_key.get_secret_value() == "sk_test_fallback"
