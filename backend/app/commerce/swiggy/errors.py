class SwiggyError(Exception):
    """Base error for the provider boundary."""


class SwiggyConfigurationError(SwiggyError):
    pass


class SwiggyAuthenticationRequired(SwiggyError):
    pass


class SwiggyProviderError(SwiggyError):
    pass


class SwiggyRateLimited(SwiggyProviderError):
    """Swiggy refused a request because the provider quota is exhausted."""

    def __init__(self, message: str, retry_after_seconds: int = 30) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(message)


class SwiggySchemaError(SwiggyError):
    pass
