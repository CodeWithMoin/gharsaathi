class MemoryDomainError(Exception):
    pass


class MemoryNotFoundError(MemoryDomainError):
    pass


class MemoryTransitionError(MemoryDomainError):
    pass


class MemoryPermissionError(MemoryDomainError):
    pass


class MemoryProhibitedUseError(MemoryDomainError):
    pass
