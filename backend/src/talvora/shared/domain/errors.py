"""Shared domain errors.

Pure Python exception classes representing domain invariant violations,
resource absence, conflicts, and authorization boundaries without framework ties.
"""

from typing import Any


class DomainError(Exception):
    """Base class for all domain errors."""

    def __init__(self, message: str, code: str = "DOMAIN_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class EntityNotFoundError(DomainError):
    """Raised when an entity does not exist or is concealed across tenants."""

    def __init__(self, entity_name: str, identifier: str) -> None:
        super().__init__(
            f"{entity_name} with identifier '{identifier}' was not found.",
            code="NOT_FOUND",
        )
        self.entity_name = entity_name
        self.identifier = identifier


class VersionConflictError(DomainError):
    """Raised when optimistic concurrency control detects a version mismatch."""

    def __init__(self, entity_name: str, expected_version: int, actual_version: int) -> None:
        super().__init__(
            f"{entity_name} version conflict: expected {expected_version}, but actual is {actual_version}.",
            code="VERSION_CONFLICT",
        )
        self.entity_name = entity_name
        self.expected_version = expected_version
        self.actual_version = actual_version


class InvalidTransitionError(DomainError):
    """Raised when an unauthorized or illegal state transition is attempted."""

    def __init__(self, state_machine: str, current_state: str, target_state: str) -> None:
        super().__init__(
            f"Cannot transition {state_machine} from '{current_state}' to '{target_state}'.",
            code="INVALID_TRANSITION",
        )
        self.state_machine = state_machine
        self.current_state = current_state
        self.target_state = target_state


class AuthorizationDeniedError(DomainError):
    """Raised when a principal lacks capability within their active membership."""

    def __init__(self, capability: str) -> None:
        super().__init__(
            f"Operation requires capability '{capability}'. Access denied.",
            code="FORBIDDEN",
        )
        self.capability = capability


class DomainValidationError(DomainError):
    """Raised when a business entity fails validation rules."""

    def __init__(self, message: str, field_errors: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR")
        self.field_errors = field_errors or {}
