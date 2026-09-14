"""Agent state (FR-4.1, NFR-3.1).

The state object is what the graph carries between nodes and what the audit
trail is written from, so it is designed to be *readable by a human reviewer*
rather than merely sufficient for the next node.

One rule governs the shape: the original request text is held in a field that
no prompt-building code touches. Masked text and raw text both exist inside
the trust boundary — the tools need real CR numbers to query the ERP — so the
separation is made explicit in the field names rather than left to discipline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.audit.models import ActorRef, Citation, ToolInvocation


class TransactionStatus(StrEnum):
    """Lifecycle of one procurement review."""

    RECEIVED = "RECEIVED"
    MASKED = "MASKED"
    GROUNDED = "GROUNDED"
    ANALYZED = "ANALYZED"
    PENDING_APPROVAL = "PENDING_APPROVAL"  # FR-4.1
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    REFUSED = "REFUSED"  # FR-2.3 — no legal basis, so no opinion


class Severity(StrEnum):
    VIOLATION = "violation"
    WARNING = "warning"
    INFO = "info"

    @property
    def arabic(self) -> str:
        return {"violation": "مخالفة", "warning": "تنبيه", "info": "معلومة"}[self.value]


@dataclass(frozen=True, slots=True)
class Finding:
    """One compliance determination, always carrying its legal basis (FR-2.2)."""

    code: str
    severity: Severity
    message_ar: str
    legal_basis: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "severity_ar": self.severity.arabic,
            "message_ar": self.message_ar,
            "legal_basis": list(self.legal_basis),
            "detail": self.detail,
        }


@dataclass(slots=True)
class TransactionFacts:
    """Structured facts pulled out of the request inside the trust boundary."""

    amount_sar: float | None = None
    vendor_cr: str | None = None
    department_id: str = ""
    subject_matter: str = ""
    local_content_ratio: float | None = None


@dataclass(slots=True)
class AgentState:
    """Everything one review run knows about itself."""

    transaction_id: str
    session_id: str
    actor: ActorRef
    role: str

    # The original text. Never read by prompt construction - see prompts.py.
    raw_request: str = ""
    # The only representation permitted to leave for the inference engine.
    masked_request: str = ""
    masking_summary: dict[str, Any] = field(default_factory=dict)

    facts: TransactionFacts = field(default_factory=TransactionFacts)

    retrieved_context: str = ""
    citations: list[Citation] = field(default_factory=list)
    is_grounded: bool = False

    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)

    findings: list[Finding] = field(default_factory=list)
    # NFR-3.1 - the visible chain of reasoning.
    reasoning_trace: list[str] = field(default_factory=list)

    status: TransactionStatus = TransactionStatus.RECEIVED
    requires_approval: bool = False
    approval_reasons: list[str] = field(default_factory=list)

    report_ar: str = ""
    refusal_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def trace(self, message: str) -> None:
        """Append one reasoning step, stamped so the order is unambiguous."""
        self.reasoning_trace.append(f"[{datetime.now(UTC):%H:%M:%S}] {message}")

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.VIOLATION]

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)

    def summary(self) -> dict[str, Any]:
        """The public shape of a review, safe to return to an authorised caller."""
        return {
            "transaction_id": self.transaction_id,
            "status": self.status.value,
            "requires_approval": self.requires_approval,
            "approval_reasons": self.approval_reasons,
            "findings": [f.to_dict() for f in self.findings],
            "citations": [c.model_dump(mode="json") for c in self.citations],
            "reasoning_trace": self.reasoning_trace,
            "report_ar": self.report_ar,
            "refusal_reason": self.refusal_reason,
            "masking": self.masking_summary,
            "tools": [t.model_dump(mode="json") for t in self.tool_invocations],
        }
