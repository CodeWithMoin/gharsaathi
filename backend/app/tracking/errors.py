class TrackingError(Exception):
    """Base error raised at the tracking provider boundary."""


class TrackingNotFound(TrackingError):
    pass


class TrackingConflict(TrackingError):
    pass


class TrackingProviderError(TrackingError):
    """A failed read from a provider tracking endpoint.

    ``retry_after_seconds`` is only used when the provider supplied it.  The
    scheduler must honour it exactly rather than layering a second delay on top.
    """

    def __init__(
        self,
        message: str = "The provider could not refresh this order right now.",
        *,
        transient: bool = True,
        retry_after_seconds: int | None = None,
        reconnect_required: bool = False,
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.retry_after_seconds = retry_after_seconds
        self.reconnect_required = reconnect_required
