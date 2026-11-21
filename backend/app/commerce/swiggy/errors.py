class SwiggyError(Exception):
    """Base error for the provider boundary."""


class SwiggyConfigurationError(SwiggyError):
    pass


class SwiggyAuthenticationRequired(SwiggyError):
    pass


class SwiggyProviderError(SwiggyError):
    pass


class SwiggySchemaError(SwiggyError):
    pass

