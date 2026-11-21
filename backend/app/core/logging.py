import logging
from typing import Any


OAUTH_CALLBACK_PATHS = {
    "/api/swiggy/oauth/callback",
    "/api/zepto/oauth/callback",
    "/callback",
}


class RedactOAuthCallbackFilter(logging.Filter):
    """Prevent OAuth codes and state values from entering Uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if not isinstance(arguments, tuple) or len(arguments) < 3:
            return True
        request_path = arguments[2]
        if not isinstance(request_path, str):
            return True
        callback_path = next(
            (
                path
                for path in OAUTH_CALLBACK_PATHS
                if request_path.startswith(f"{path}?")
            ),
            None,
        )
        if callback_path is not None:
            mutable: list[Any] = list(arguments)
            mutable[2] = f"{callback_path}?[REDACTED]"
            record.args = tuple(mutable)
        return True


def install_sensitive_log_filters() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactOAuthCallbackFilter) for item in access_logger.filters):
        access_logger.addFilter(RedactOAuthCallbackFilter())
