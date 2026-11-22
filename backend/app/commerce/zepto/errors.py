class ZeptoError(Exception):
    """Base exception for the Zepto provider boundary."""


class ZeptoConfigurationError(ZeptoError):
    pass


class ZeptoAuthenticationRequired(ZeptoError):
    pass


class ZeptoProviderError(ZeptoError):
    # A content-free category the assistant may act on; never provider text.
    reason = "unavailable"


class ZeptoRateLimited(ZeptoProviderError):
    reason = "rate_limited"


class ZeptoSessionExpired(ZeptoProviderError):
    reason = "session_expired"


class ZeptoAddressInvalid(ZeptoProviderError):
    """The selected saved address is no longer in the Zepto account."""

    reason = "address_invalid"


class ZeptoSchemaError(ZeptoError):
    pass
