"""Source and decoding failures expressed without transport-library exceptions."""

from enum import Enum


class FailureKind(Enum):
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    NOT_FOUND = "not_found"
    NO_CONTENT = "no_content"
    REQUEST_REJECTED = "request_rejected"
    TRANSIENT = "transient"
    OTHER = "other"


class SourceError(RuntimeError):
    def __init__(self, kind: FailureKind, message: str):
        super().__init__(message)
        self.kind = kind

    @property
    def fatal(self) -> bool:
        return self.kind in (FailureKind.AUTHENTICATION, FailureKind.PERMISSION, FailureKind.RATE_LIMIT)

    @property
    def can_split_range(self) -> bool:
        return self.kind in (FailureKind.REQUEST_REJECTED, FailureKind.TRANSIENT)

    @property
    def missing_file(self) -> bool:
        return self.kind in (FailureKind.NOT_FOUND, FailureKind.NO_CONTENT)


class DecodeError(ValueError):
    pass
