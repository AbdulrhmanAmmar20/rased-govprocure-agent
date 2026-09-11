"""Human-in-the-loop approval (FR-4.1, FR-4.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agent.service import decide_approval, store
from app.api.deps import current_principal, require
from app.api.schemas import ApprovalRequest, ApprovalResponse, PendingTransaction
from app.core.exceptions import RasedError
from app.core.rbac import Permission
from app.core.security import Principal

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get(
    "/pending",
    response_model=list[PendingTransaction],
    summary="المعاملات المعلقة بانتظار الاعتماد",
    dependencies=[Depends(require(Permission.APPROVE_TRANSACTION))],
)
async def pending() -> list[PendingTransaction]:
    """The Director's queue.

    Summaries only — the reasons for suspension and a violation count. The
    full report is one call away, so opening a case is a distinct act rather
    than a side effect of glancing at the list.
    """
    return [
        PendingTransaction(
            transaction_id=state.transaction_id,
            status=state.status.value,
            approval_reasons=state.approval_reasons,
            violation_count=len(state.violations),
            created_at=state.created_at.isoformat(),
        )
        for state in store.pending()
    ]


@router.post(
    "/{transaction_id}/decide",
    response_model=ApprovalResponse,
    summary="اعتماد أو رفض معاملة معلقة",
)
async def decide(
    transaction_id: str,
    payload: ApprovalRequest,
    principal: Principal = Depends(current_principal),
) -> ApprovalResponse:
    """FR-4.2 — record the Director's decision with their notes.

    Authority is checked inside decide_approval, which also writes the audit
    record, for the same reason as the disclosure route: the check and the
    record belong to one another.

    A rejection is as final as an approval here. Both close the transaction and
    destroy its masking table; re-submitting starts a fresh review with a fresh
    audit chain rather than silently reopening a decided case.
    """
    try:
        state = decide_approval(
            transaction_id, principal, approved=payload.approved, notes=payload.notes
        )
    except RasedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc

    return ApprovalResponse(
        transaction_id=state.transaction_id,
        status=state.status.value,
        approver=principal.subject,
        notes=payload.notes,
    )
