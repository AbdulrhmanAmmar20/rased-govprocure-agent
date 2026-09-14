"""Audit trail access for the Compliance Auditor (FR-5.1)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require
from app.api.schemas import AuditRecordModel, IntegrityResponse
from app.audit.models import AuditEvent
from app.audit.trail import get_trail
from app.core.exceptions import AuditIntegrityError
from app.core.rbac import Permission

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require(Permission.READ_AUDIT_LOG))],
)


@router.get("/records", response_model=list[AuditRecordModel], summary="استعراض سجل التدقيق")
async def records(
    transaction_id: str | None = Query(default=None),
    event: AuditEvent | None = Query(default=None),
    actor_subject: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[AuditRecordModel]:
    """Filtered records.

    The projection omits masked_prompt and tool arguments. Those are in the
    file and reachable by a deliberate read of it; a list endpoint that
    returned full prompts by default would make casual browsing of everyone's
    submissions the easiest thing an auditor could do.
    """
    found = get_trail().query(
        transaction_id=transaction_id,
        event=event,
        actor_subject=actor_subject,
        limit=limit,
    )
    return [
        AuditRecordModel(
            sequence=record.sequence,
            record_id=record.record_id,
            timestamp_utc=record.timestamp_utc.isoformat(),
            transaction_id=record.transaction_id,
            event=record.event.value,
            actor_subject=record.actor.subject,
            actor_role=record.actor.role,
            outcome=record.outcome,
            record_hash=record.record_hash,
        )
        for record in found
    ]


@router.get(
    "/transaction/{transaction_id}",
    summary="مسار معاملة كاملاً مع مسار تفكير الوكيل",
)
async def transaction_history(transaction_id: str) -> dict:
    """Everything recorded about one transaction, including the NFR-3.1 trace.

    Scoped to a single transaction on purpose: an auditor examining a specific
    case gets the full picture, without that being the same operation as
    reading every case at once.
    """
    found = get_trail().query(transaction_id=transaction_id, limit=1000)
    if not found:
        raise HTTPException(
            status_code=404,
            detail={"code": "transaction_not_found", "message": "لا توجد سجلات لهذه المعاملة."},
        )
    return {
        "transaction_id": transaction_id,
        "record_count": len(found),
        "records": [record.model_dump(mode="json") for record in found],
    }


@router.get("/verify", response_model=IntegrityResponse, summary="التحقق من سلامة سلسلة التدقيق")
async def verify_chain() -> IntegrityResponse:
    """Re-walk the hash chain and report the first break, if any."""
    try:
        count = get_trail().verify()
    except AuditIntegrityError as exc:
        return IntegrityResponse(
            verified=False,
            record_count=int(exc.details.get("sequence", 0)),
            message=f"{exc.message} (التفاصيل: {exc.details})",
        )
    return IntegrityResponse(
        verified=True, record_count=count, message="سلسلة سجل التدقيق سليمة."
    )
