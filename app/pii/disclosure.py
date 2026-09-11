"""The only supported way to turn placeholders back into real values (FR-1.3).

:meth:`app.pii.engine.MaskingEngine.unmask` deliberately performs no
authorisation check. Concentrating the check here means there is exactly one
door onto the original data, and it is a door that always leaves a trace.

Disclosure is itself an auditable event: reading a citizen's identity number
is an action a supervisor may later need to account for, not a free read.
"""

from __future__ import annotations

from app.audit.models import ActorRef, AuditEvent
from app.audit.trail import AuditTrail, get_trail
from app.core.logging import get_logger
from app.core.rbac import Permission
from app.core.security import Principal
from app.pii.engine import get_engine
from app.pii.vault import registry

logger = get_logger("rased.pii.disclosure")


def reveal(
    text: str,
    *,
    session_id: str,
    principal: Principal,
    transaction_id: str = "",
    trail: AuditTrail | None = None,
) -> str:
    """Restore original values for an authorised caller, and record that it happened.

    Raises AuthorizationError when the caller lacks ``pii:reveal`` — which is
    the case for the System Admin role by design.
    """
    principal.require(Permission.REVEAL_MASKED_DATA)

    vault = registry.get(session_id)
    placeholders = vault.placeholders() if vault else []
    disclosed = [p for p in placeholders if p in text]

    revealed = get_engine().unmask(text, session_id=session_id)

    if disclosed:
        (trail or get_trail()).append(
            transaction_id=transaction_id or session_id,
            event=AuditEvent.PII_REVEALED,
            actor=ActorRef(**principal.audit_identity()),
            outcome="disclosed",
            # Placeholder names only. Recording what was behind them would put
            # the very values FR-1 removes back into the permanent record.
            detail={"placeholders": disclosed, "count": len(disclosed)},
        )
        logger.info(
            "masked values disclosed to authorised caller",
            extra={
                "session_id": session_id,
                "subject": principal.subject,
                "role": principal.role.value,
                "count": len(disclosed),
            },
        )

    return revealed


def close_session(session_id: str) -> None:
    """Destroy the reverse mapping once a transaction is finished."""
    registry.discard(session_id)
