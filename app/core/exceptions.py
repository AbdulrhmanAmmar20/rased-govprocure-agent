"""Domain exceptions.

Each carries an HTTP status and a stable machine code so the API layer can
translate without a pile of isinstance checks, and so the audit trail records
a code rather than a free-text message that may drift between releases.
"""

from __future__ import annotations

from typing import Any


class RasedError(Exception):
    """Base class for every error Rased raises deliberately."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class AuthenticationError(RasedError):
    """Token missing, malformed, expired or signed by an unknown key."""

    status_code = 401
    code = "authentication_failed"


class AuthorizationError(RasedError):
    """Caller is authenticated but their role does not carry the permission.

    Raised by the tool gateway in FR-3.2 — for example a Specialist reaching
    for ``create_procurement_order``.
    """

    status_code = 403
    code = "authorization_denied"


class ResidencyViolationError(RasedError):
    """An outbound call would leave the Kingdom (NFR-1.1)."""

    status_code = 503
    code = "residency_violation"


class MaskingError(RasedError):
    """The PII engine could not guarantee the payload was masked (FR-1)."""

    status_code = 500
    code = "masking_failed"


class UngroundedAnswerError(RasedError):
    """The agent produced a finding with no retrievable legal basis (FR-2.3)."""

    status_code = 422
    code = "ungrounded_answer"


class ToolExecutionError(RasedError):
    """A registered tool failed while executing."""

    status_code = 400
    code = "tool_execution_failed"


class ApprovalRequiredError(RasedError):
    """The transaction is suspended pending a human decision (FR-4.1)."""

    status_code = 409
    code = "approval_required"


class AuditIntegrityError(RasedError):
    """The append-only audit chain failed verification (FR-5.1)."""

    status_code = 500
    code = "audit_integrity_violation"
