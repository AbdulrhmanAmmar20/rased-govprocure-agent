"""Audit record schema (FR-5.1).

Every field FR-5.1 enumerates has a home here: who acted and in what role, the
UTC stamp, the prompt *after* masking, every tool the agent reached for with
its arguments, the human decision, and the final outcome.

Two design rules hold throughout:

* The record carries masked text only. An audit trail that quietly accumulated
  the identity numbers the gate just removed would defeat FR-1 at the point it
  is hardest to notice.
* Records are append-only and chained. :mod:`app.audit.trail` supplies the
  hashes; this module defines the shape they cover.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(StrEnum):
    """Every state change worth reconstructing after the fact."""

    TRANSACTION_SUBMITTED = "transaction.submitted"
    PII_MASKED = "pii.masked"
    PII_REVEALED = "pii.revealed"
    REGULATION_RETRIEVED = "regulation.retrieved"
    AGENT_REASONED = "agent.reasoned"
    TOOL_INVOKED = "tool.invoked"
    TOOL_DENIED = "tool.denied"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_REJECTED = "approval.rejected"
    REPORT_ISSUED = "report.issued"
    VIOLATION_DETECTED = "violation.detected"
    INTEGRITY_VERIFIED = "integrity.verified"


class ActorRef(BaseModel):
    """Who performed the action (FR-5.1: employee id, role)."""

    subject: str
    role: str
    department_id: str = ""
    token_id: str = ""


class ToolInvocation(BaseModel):
    """A tool the agent attempted, recorded whether or not it was permitted.

    Denied attempts are the interesting ones for an auditor: they are the
    evidence that FR-3.2 held. Storing only successes would erase exactly the
    events the control exists to produce.
    """

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    allowed: bool
    outcome: str = ""
    denial_reason: str = ""
    duration_ms: float = 0.0


class Citation(BaseModel):
    """The legal basis attached to a finding (FR-2.2)."""

    document: str
    article: str
    excerpt: str = ""
    score: float = 0.0


class ApprovalDecision(BaseModel):
    """The human decision recorded against a suspended transaction (FR-4.2)."""

    status: str = "pending"
    approver_subject: str = ""
    approver_role: str = ""
    notes: str = ""
    decided_at: datetime | None = None


class AuditRecord(BaseModel):
    """One immutable entry in the chain."""

    record_id: str
    sequence: int
    timestamp_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    transaction_id: str
    event: AuditEvent
    actor: ActorRef

    # FR-5.1 — the masked prompt, never the original.
    masked_prompt: str = ""
    entity_counts: dict[str, int] = Field(default_factory=dict)

    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    # NFR-3.1 — the agent's reasoning path, so a reviewer can see why.
    reasoning_trace: list[str] = Field(default_factory=list)

    approval: ApprovalDecision | None = None
    outcome: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)

    # Chain linkage, filled in by the trail writer.
    previous_hash: str = ""
    record_hash: str = ""

    model_config = {"frozen": True}

    def hashable_payload(self) -> dict[str, Any]:
        """The record minus its own hash, which is what the digest covers."""
        data = self.model_dump(mode="json")
        data.pop("record_hash", None)
        return data
