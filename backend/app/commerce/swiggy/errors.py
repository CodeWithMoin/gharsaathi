class SwiggyError(Exception):
    """Base error for the provider boundary."""


class SwiggyConfigurationError(SwiggyError):
    pass


class SwiggyAuthenticationRequired(SwiggyError):
    pass


class SwiggyProviderError(SwiggyError):
    pass


class SwiggyRateLimitError(SwiggyError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(
            "Swiggy is receiving too many requests. "
            f"Try again in {self.retry_after_seconds} seconds."
        )


class SwiggySchemaError(SwiggyError):
    pass
