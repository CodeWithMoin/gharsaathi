class ZeptoError(Exception):
    """Base exception for the Zepto provider boundary."""


class ZeptoConfigurationError(ZeptoError):
    pass


class ZeptoAuthenticationRequired(ZeptoError):
    pass


class ZeptoProviderError(ZeptoError):
    pass


class ZeptoRateLimited(ZeptoProviderError):
    pass


class ZeptoSchemaError(ZeptoError):
    pass
