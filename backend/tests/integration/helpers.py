def as_user(user_id: str) -> dict[str, str]:
    """Identity header understood by the Clerk override in tests/conftest.py."""

    return {"X-Test-User-Id": user_id}
