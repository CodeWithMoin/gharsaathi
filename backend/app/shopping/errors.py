class ShoppingError(Exception):
    """Base error for basket and approval business rules."""


class NotFound(ShoppingError):
    pass


class InvalidBasketChange(ShoppingError):
    pass


class BasketConflict(InvalidBasketChange):
    """Another writer changed the basket revision first; re-read and retry."""


class ApprovalRefused(ShoppingError):
    """The basket cannot be approved in its current state."""


class ProviderNotConnected(ShoppingError):
    pass


class ProviderUnavailable(ShoppingError):
    """The provider failed before any consequential action."""
