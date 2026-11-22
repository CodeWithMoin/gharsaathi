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


class ZeptoUnrecognisedResponse(ZeptoProviderError):
    """A read answered in a shape the parser does not know.

    It says nothing about the products (an unknown shape is not "no results"),
    so the user hears the same line as for an unreachable store.
    """

    reason = "unavailable"
