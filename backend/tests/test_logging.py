import logging

from app.core.logging import OAUTH_CALLBACK_PATH, RedactOAuthCallbackFilter


def test_oauth_callback_query_is_redacted_from_uvicorn_access_log() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:12345",
            "GET",
            f"{OAUTH_CALLBACK_PATH}?code=secret-code&state=secret-state",
            "1.1",
            302,
        ),
        exc_info=None,
    )

    assert RedactOAuthCallbackFilter().filter(record) is True
    rendered = record.getMessage()
    assert "secret-code" not in rendered
    assert "secret-state" not in rendered
    assert f"{OAUTH_CALLBACK_PATH}?[REDACTED]" in rendered


def test_non_callback_access_log_is_unchanged() -> None:
    arguments = ("127.0.0.1:12345", "GET", "/health", "1.1", 200)
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=arguments,
        exc_info=None,
    )

    RedactOAuthCallbackFilter().filter(record)
    assert record.args == arguments

