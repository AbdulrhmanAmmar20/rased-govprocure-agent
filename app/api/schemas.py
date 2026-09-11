"""API request and response models.

Responses are shaped so that a caller never receives unmasked data by
accident. The review response carries the masked prompt and placeholder names;
original values are only ever returned from the explicit disclosure endpoint,
which checks pii:reveal and writes an audit record.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.rbac import Role


class TokenRequest(BaseModel):
    """Demo-only identity assertion.

    In production this endpoint does not exist: the token is minted from the
    entity's IAM / Nafath federation assertion. It is kept here so the SRS
    scenario can be walked through without standing up an identity provider,
    and app.main refuses to mount it outside development.
    """

    subject: str = Field(..., examples=["emp-1042"])
    role: Role
    department_id: str = Field(default="IT-DEPT")
    display_name: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    permissions: list[str]


class ReviewRequest(BaseModel):
    """A procurement request submitted for compliance review."""

    text: str = Field(
        ...,
        min_length=8,
        max_length=20_000,
        description="نص طلب الشراء كما كتبه الموظف.",
        examples=[
            "أرغب في ترسية شراء مباشر لكاميرات مراقبة على مؤسسة الأفق "
            "سجل تجاري 1010998877 بمبلغ 120,000 ريال وفق نظام المنافسات."
        ],
    )
    transaction_id: str | None = Field(
        default=None, description="معرّف اختياري للمعاملة؛ يُولَّد تلقائياً إن لم يُرسل."
    )


class FindingModel(BaseModel):
    code: str
    severity: str
    severity_ar: str
    message_ar: str
    legal_basis: list[str]
    detail: dict[str, Any] = Field(default_factory=dict)


class CitationModel(BaseModel):
    document: str
    article: str
    excerpt: str = ""
    score: float = 0.0


class ToolInvocationModel(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    allowed: bool
    outcome: str = ""
    denial_reason: str = ""
    duration_ms: float = 0.0


class ReviewResponse(BaseModel):
    transaction_id: str
    status: str
    requires_approval: bool
    approval_reasons: list[str] = Field(default_factory=list)
    masked_request: str
    report_ar: str = ""
    refusal_reason: str = ""
    findings: list[FindingModel] = Field(default_factory=list)
    citations: list[CitationModel] = Field(default_factory=list)
    tools: list[ToolInvocationModel] = Field(default_factory=list)
    reasoning_trace: list[str] = Field(default_factory=list)
    masking: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    """FR-4.2 — the Director's decision."""

    approved: bool
    notes: str = Field(default="", max_length=4000)


class ApprovalResponse(BaseModel):
    transaction_id: str
    status: str
    approver: str
    notes: str = ""


class PendingTransaction(BaseModel):
    transaction_id: str
    status: str
    approval_reasons: list[str]
    violation_count: int
    created_at: str


class DisclosureRequest(BaseModel):
    """Ask for placeholders in a piece of text to be resolved (FR-1.3)."""

    transaction_id: str
    text: str = Field(..., max_length=20_000)


class DisclosureResponse(BaseModel):
    transaction_id: str
    revealed_text: str


class AuditRecordModel(BaseModel):
    sequence: int
    record_id: str
    timestamp_utc: str
    transaction_id: str
    event: str
    actor_subject: str
    actor_role: str
    outcome: str
    record_hash: str


class IntegrityResponse(BaseModel):
    verified: bool
    record_count: int
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    region: str


class ReadinessResponse(BaseModel):
    status: str
    region: str
    corpus_version: str
    corpus_status: str
    corpus_documents: int
    inference_endpoint: str
    inference_available: bool
    vector_backend: str
    langgraph_installed: bool
    presidio_installed: bool
