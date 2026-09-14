"""Orchestration: run a review, and resolve a suspended one (FR-4.1, FR-4.2).

Suspended transactions are held in a process-local store. That is honest for a
single-instance deployment and wrong for the horizontally scaled one NFR-2.3
calls for - a Director's approval would have to land on the same pod that ran
the review. The interface is deliberately narrow (get/put/list) so moving it
to Redis or Postgres is a substitution rather than a rewrite; see
docs/architecture.md for the intended production binding.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

from app.agent.graph import ComplianceGraph
from app.agent.nodes import GraphContext
from app.agent.state import AgentState, TransactionStatus
from app.audit.models import ActorRef, ApprovalDecision, AuditEvent
from app.audit.trail import AuditTrail, get_trail
from app.core.exceptions import ApprovalRequiredError, RasedError
from app.core.logging import get_logger
from app.core.rbac import Permission
from app.core.security import Principal
from app.pii.disclosure import close_session

logger = get_logger("rased.service")


class TransactionNotFoundError(RasedError):
    status_code = 404
    code = "transaction_not_found"


class TransactionStore:
    """Holds runs so a suspended one can be resumed by another request."""

    def __init__(self) -> None:
        self._items: dict[str, AgentState] = {}
        self._lock = threading.Lock()

    def put(self, state: AgentState) -> None:
        with self._lock:
            self._items[state.transaction_id] = state

    def get(self, transaction_id: str) -> AgentState | None:
        with self._lock:
            return self._items.get(transaction_id)

    def pending(self) -> list[AgentState]:
        with self._lock:
            return [
                s for s in self._items.values()
                if s.status is TransactionStatus.PENDING_APPROVAL
            ]


store = TransactionStore()


def review_transaction(
    request_text: str,
    principal: Principal,
    *,
    transaction_id: str | None = None,
    trail: AuditTrail | None = None,
    graph: ComplianceGraph | None = None,
    ctx: GraphContext | None = None,
) -> AgentState:
    """Run one procurement review end to end."""
    principal.require(Permission.SUBMIT_PROCUREMENT_REQUEST)

    if not transaction_id:
        stamp = f"{datetime.now(UTC):%Y%m%d}"
        transaction_id = f"TX-{stamp}-{uuid.uuid4().hex[:8].upper()}"
    trail = trail or get_trail()
    actor = ActorRef(**principal.audit_identity())

    state = AgentState(
        transaction_id=transaction_id,
        session_id=transaction_id,
        actor=actor,
        role=principal.role.value,
        raw_request=request_text,
    )

    trail.append(
        transaction_id=transaction_id,
        event=AuditEvent.TRANSACTION_SUBMITTED,
        actor=actor,
        outcome="received",
        # The original text is never written here. Its masked form is recorded
        # by mask_node a moment later, which is what FR-5.1 asks for.
        detail={"request_length": len(request_text)},
    )

    context = ctx or GraphContext.build(principal)
    context.trail = trail

    state = (graph or ComplianceGraph()).run(state, context)
    store.put(state)

    logger.info(
        "review complete",
        extra={
            "transaction_id": transaction_id,
            "status": state.status.value,
            "violations": len(state.violations),
        },
    )
    return state


def decide_approval(
    transaction_id: str,
    principal: Principal,
    *,
    approved: bool,
    notes: str = "",
    trail: AuditTrail | None = None,
) -> AgentState:
    """FR-4.2 - a Director approves or rejects a suspended transaction."""
    principal.require(Permission.APPROVE_TRANSACTION)

    state = store.get(transaction_id)
    if state is None:
        raise TransactionNotFoundError(
            "لا توجد معاملة بهذا المعرّف.", transaction_id=transaction_id
        )
    if state.status is not TransactionStatus.PENDING_APPROVAL:
        raise ApprovalRequiredError(
            "المعاملة ليست في حالة انتظار الاعتماد.",
            transaction_id=transaction_id,
            status=state.status.value,
        )

    decision = ApprovalDecision(
        status="approved" if approved else "rejected",
        approver_subject=principal.subject,
        approver_role=principal.role.value,
        notes=notes,
        decided_at=datetime.now(UTC),
    )

    state.status = TransactionStatus.APPROVED if approved else TransactionStatus.REJECTED
    verdict = "اعتمد" if approved else "رفض"
    state.trace(
        f"{verdict} صاحب الصلاحية {principal.subject} المعاملة. "
        f"ملاحظات: {notes or 'لا يوجد'}"
    )

    (trail or get_trail()).append(
        transaction_id=transaction_id,
        event=AuditEvent.APPROVAL_GRANTED if approved else AuditEvent.APPROVAL_REJECTED,
        actor=ActorRef(**principal.audit_identity()),
        approval=decision,
        outcome=state.status.value,
        detail={"reasons": state.approval_reasons},
    )

    store.put(state)

    # Once a decision is recorded the mapping table has no further purpose, so
    # it is destroyed rather than left to age out (FR-1.3).
    close_session(state.session_id)
    return state
