"""Typed errors, mapped from Loamhand's status codes and its error body.

The server answers every error as ``{"error": "<sentence>", "code": "<machine code>"}``
(plus an optional ``details`` object).
"""

from __future__ import annotations

from typing import Any


class LoamhandError(Exception):
    """Base class for everything this library raises on purpose."""


class LoamhandConnectionError(LoamhandError):
    """The server could not be reached, or did not answer in time."""


class LoamhandApiError(LoamhandError):
    """The server answered with an error."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details


class LoamhandAuthError(LoamhandApiError):
    """401: the token is invalid, or its connection was revoked."""


class LoamhandPlanError(LoamhandApiError):
    """409 ``plan_limit``: the account no longer holds the Sensors add-on.

    Nothing is lost on the server; it simply refuses new readings until the add-on
    is back.
    """


class LoamhandRateLimitError(LoamhandApiError):
    """429: too many requests. ``retry_after`` is the server's hint in seconds, if sent."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class LoamhandValidationError(LoamhandApiError):
    """422 (or a malformed 400): the server refused the content and says why in ``message``."""


class LoamhandInvalidCodeError(LoamhandApiError):
    """400 ``invalid_code``: the claim code is wrong, already used, or has expired."""


class LoamhandConflictError(LoamhandApiError):
    """409 other than ``plan_limit``; ``try_again`` means a race that a retry resolves."""


class LoamhandDeviceRetiredError(LoamhandConflictError):
    """409 ``device_retired``: the gardener retired this device in Loamhand."""


class LoamhandUnknownDeviceError(LoamhandApiError):
    """404 ``unknown_device``: readings for a device the connection has not declared."""


class LoamhandServerError(LoamhandApiError):
    """5xx: Loamhand failed. Retrying later is reasonable."""
