"""Procurement review (FR-1 through FR-3, FR-5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agent.service import TransactionNotFoundError, review_transaction, store
from app.api.deps import current_principal, require
from app.api.schemas import (
    DisclosureRequest,
    DisclosureResponse,
    ReviewRequest,
    ReviewResponse,
)
from app.core.exceptions import RasedError
from app.core.rbac import Permission
from app.core.security import Principal
from app.pii.disclosure import reveal
from app.pii.engine import get_engine

router = APIRouter(prefix="/procurement", tags=["procurement"])


def _redact_tools(tools: list[dict], session_id: str) -> list[dict]:
    """Re-mask recorded tool arguments before they cross the HTTP boundary.

    Tool arguments hold real identifiers by design - check_vendor_eligibility
    cannot query the ERP with a placeholder. Returning them verbatim would let
    any caller holding regulations:query read original values out of the tool
    log without passing the pii:reveal check or leaving a disclosure record,
    which is precisely the bypass FR-1.3 exists to prevent.
    """
    engine = get_engine()
    redacted: list[dict] = []
    for tool in tools:
        arguments = {
            key: engine.remask(value, session_id=session_id) if isinstance(value, str) else value
            for key, value in tool.get("arguments", {}).items()
        }
        redacted.append({**tool, "arguments": arguments})
    return redacted


def _to_response(state) -> ReviewResponse:
    summary = state.summary()
    return ReviewResponse(
        transaction_id=summary["transaction_id"],
        status=summary["status"],
        requires_approval=summary["requires_approval"],
        approval_reasons=summary["approval_reasons"],
        masked_request=state.masked_request,
        report_ar=summary["report_ar"],
        refusal_reason=summary["refusal_reason"],
        findings=summary["findings"],
        citations=summary["citations"],
        tools=_redact_tools(summary["tools"], state.session_id),
        reasoning_trace=summary["reasoning_trace"],
        masking=summary["masking"],
    )


@router.post(
    "/review",
    response_model=ReviewResponse,
    summary="فحص طلب شراء ومطابقته للنظام",
    dependencies=[Depends(require(Permission.SUBMIT_PROCUREMENT_REQUEST))],
)
async def review(
    payload: ReviewRequest,
    principal: Principal = Depends(current_principal),
) -> ReviewResponse:
    """Mask, ground, check, evaluate and report on one procurement request.

    The response never contains the original identifiers. What comes back is
    the masked text plus placeholder names; resolving them is a separate,
    audited call to /procurement/reveal.
    """
    try:
        state = review_transaction(
            payload.text, principal, transaction_id=payload.transaction_id
        )
    except RasedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return _to_response(state)


@router.get(
    "/{transaction_id}",
    response_model=ReviewResponse,
    summary="استعراض نتيجة فحص معاملة",
    dependencies=[Depends(require(Permission.QUERY_REGULATIONS))],
)
async def get_transaction(transaction_id: str) -> ReviewResponse:
    state = store.get(transaction_id)
    if state is None:
        exc = TransactionNotFoundError(
            "لا توجد معاملة بهذا المعرّف.", transaction_id=transaction_id
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _to_response(state)


@router.post(
    "/reveal",
    response_model=DisclosureResponse,
    summary="إظهار البيانات المحجوبة لمستخدم مصرّح له",
)
async def reveal_masked(
    payload: DisclosureRequest,
    principal: Principal = Depends(current_principal),
) -> DisclosureResponse:
    """FR-1.3 — resolve placeholders for a caller holding pii:reveal.

    The permission is checked inside reveal() rather than by a route
    dependency, because that same function also writes the disclosure audit
    record. Splitting the check from the record would make it possible to add
    a second caller later that passes the guard and logs nothing.
    """
    state = store.get(payload.transaction_id)
    if state is None:
        exc = TransactionNotFoundError(
            "لا توجد معاملة بهذا المعرّف.", transaction_id=payload.transaction_id
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())

    try:
        revealed = reveal(
            payload.text,
            session_id=state.session_id,
            principal=principal,
            transaction_id=payload.transaction_id,
        )
    except RasedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc

    return DisclosureResponse(
        transaction_id=payload.transaction_id, revealed_text=revealed
    )
