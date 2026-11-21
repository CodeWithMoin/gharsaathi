class SupportError(Exception):
    """Base error for the support complaint workflow."""


class ComplaintNotFound(SupportError):
    pass


class ComplaintStateError(SupportError):
    pass


class EvidenceValidationError(SupportError):
    pass


class SupportChannelUnavailable(SupportError):
    pass
