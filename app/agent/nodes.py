"""Graph nodes.

Each node does one thing, mutates the state, and writes what it did to the
audit trail. Keeping them small and independently callable is what makes the
graph testable without a graph runtime, and what lets an auditor read a
transaction's history as a sequence of named steps rather than one opaque run.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.extraction import extract_facts
from app.agent.rules import evaluate, requires_human_approval
from app.agent.state import AgentState, TransactionStatus
from app.audit.models import AuditEvent
from app.audit.trail import AuditTrail, get_trail
from app.core.exceptions import UngroundedAnswerError
from app.core.logging import get_logger
from app.core.security import Principal
from app.pii.engine import MaskingEngine, get_engine
from app.rag.retriever import RetrievalResult, Retriever
from app.tools.catalogue import get_gateway
from app.tools.registry import ToolGateway

logger = get_logger("rased.agent")


@dataclass(slots=True)
class GraphContext:
    """Collaborators a run needs. Injected so tests can substitute any of them."""

    principal: Principal
    gateway: ToolGateway
    retriever: Retriever
    trail: AuditTrail
    engine: MaskingEngine
    retrieval: RetrievalResult | None = None

    @classmethod
    def build(cls, principal: Principal, *, retriever: Retriever | None = None) -> GraphContext:
        return cls(
            principal=principal,
            gateway=get_gateway(),
            retriever=retriever or Retriever(),
            trail=get_trail(),
            engine=get_engine(),
        )


def mask_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """FR-1 - mask the request and derive the facts the checks need."""
    result = ctx.engine.mask(state.raw_request, session_id=state.session_id)

    state.masked_request = result.masked_text
    state.masking_summary = result.audit_summary()
    state.facts = extract_facts(
        state.raw_request,
        detected_entities=result.entities,
        department_fallback=ctx.principal.department_id,
    )
    state.status = TransactionStatus.MASKED
    state.trace(
        f"حُجبت {len(result.entities)} بيانات حساسة خلال {result.duration_ms:.1f} مللي ثانية."
    )

    ctx.trail.append(
        transaction_id=state.transaction_id,
        event=AuditEvent.PII_MASKED,
        actor=state.actor,
        masked_prompt=result.masked_text,
        entity_counts=result.entity_counts,
        outcome="masked",
        detail={
            "placeholders": sorted(result.placeholders),
            "masking_duration_ms": round(result.duration_ms, 2),
            "within_latency_budget": result.within_budget,
        },
    )
    return state


def retrieve_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """FR-2 - fetch the legal basis, or refuse if there is none."""
    query = " ".join(
        part
        for part in [
            state.facts.subject_matter,
            "الشراء المباشر السقف النظامي المحتوى المحلي أهلية المورد",
        ]
        if part
    )
    retrieval = ctx.retriever.retrieve(query)
    ctx.retrieval = retrieval

    state.retrieved_context = retrieval.context_block()
    state.citations = retrieval.citations()
    state.is_grounded = retrieval.is_grounded

    ctx.trail.append(
        transaction_id=state.transaction_id,
        event=AuditEvent.REGULATION_RETRIEVED,
        actor=state.actor,
        citations=state.citations,
        outcome="grounded" if retrieval.is_grounded else "ungrounded",
        detail={"top_score": round(retrieval.top_score, 4), "hits": len(retrieval.hits)},
    )

    try:
        ctx.retriever.require_grounding(retrieval)
    except UngroundedAnswerError as exc:
        state.status = TransactionStatus.REFUSED
        state.refusal_reason = exc.message
        state.trace("لا يوجد سند نظامي في قاعدة اللوائح؛ امتنع الوكيل عن إصدار رأي.")
        return state

    state.status = TransactionStatus.GROUNDED
    state.trace(
        f"استُرجعت {len(retrieval.hits)} مواد نظامية، أعلاها مطابقة "
        f"{retrieval.hits[0].chunk.article}."
    )
    return state


def compliance_tools_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """FR-3.1 - run the read-only checks the verdict depends on."""
    calls: list[tuple[str, dict]] = []

    if state.facts.vendor_cr:
        calls.append(("check_vendor_eligibility", {"cr_number": state.facts.vendor_cr}))
    if state.facts.amount_sar is not None:
        calls.append(
            (
                "verify_budget_ceiling",
                {
                    "department_id": state.facts.department_id or ctx.principal.department_id,
                    "amount": state.facts.amount_sar,
                },
            )
        )

    for name, arguments in calls:
        result, invocation = ctx.gateway.invoke(name, arguments, principal=ctx.principal)
        state.tool_invocations.append(invocation)
        if result.ok:
            state.tool_results[name] = result.data
            state.trace(f"نُفّذت الأداة {name} بنجاح.")
        else:
            state.trace(f"تعذر تنفيذ الأداة {name}: {result.error}")

        ctx.trail.append(
            transaction_id=state.transaction_id,
            event=AuditEvent.TOOL_INVOKED if invocation.allowed else AuditEvent.TOOL_DENIED,
            actor=state.actor,
            tool_invocations=[invocation],
            outcome=result.status,
        )

    return state


def evaluate_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """Apply the deterministic rules and decide whether a human must see this."""
    state.findings = evaluate(state)
    state.requires_approval, state.approval_reasons = requires_human_approval(state)
    state.status = TransactionStatus.ANALYZED

    for finding in state.violations:
        state.trace(f"مخالفة: {finding.code} - {finding.message_ar}")
        ctx.trail.append(
            transaction_id=state.transaction_id,
            event=AuditEvent.VIOLATION_DETECTED,
            actor=state.actor,
            outcome=finding.code,
            detail=finding.to_dict(),
        )

    if not state.violations:
        state.trace("لم تُرصد مخالفات نظامية في الفحوصات المنفذة.")
    return state


def order_attempt_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """Attempt the order the request asked for, and record the outcome.

    This node exists because the SRS acceptance scenario turns on it: the agent
    must genuinely attempt create_procurement_order so the gateway's refusal is
    a recorded event rather than a hypothetical. Skipping the attempt when the
    caller obviously lacks authority would produce a cleaner run and a weaker
    audit trail - there would be no evidence that FR-3.2 was ever exercised.
    """
    if state.facts.vendor_cr is None or state.facts.amount_sar is None:
        return state

    arguments = {
        "vendor_id": state.facts.vendor_cr,
        "amount": state.facts.amount_sar,
        "contract_terms": state.facts.subject_matter,
        "department_id": state.facts.department_id or ctx.principal.department_id,
    }
    result, invocation = ctx.gateway.invoke(
        "create_procurement_order", arguments, principal=ctx.principal
    )
    state.tool_invocations.append(invocation)

    if result.denied:
        state.trace(
            "مُنع إنشاء أمر الشراء: الدور الحالي لا يملك صلاحية إصدار أوامر الشراء."
        )
    elif result.ok:
        state.tool_results["create_procurement_order"] = result.data
        status = result.data.get("status")
        state.trace(f"نتيجة محاولة إنشاء أمر الشراء: {status}.")

    ctx.trail.append(
        transaction_id=state.transaction_id,
        event=AuditEvent.TOOL_INVOKED if invocation.allowed else AuditEvent.TOOL_DENIED,
        actor=state.actor,
        tool_invocations=[invocation],
        outcome=result.status,
        detail={"denied": result.denied},
    )
    return state


def report_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """Write the Arabic compliance report and record it."""
    from app.agent.report import generate_report

    retrieval = ctx.retrieval
    if retrieval is None:
        state.report_ar = ""
        return state

    report, source = generate_report(state, retrieval)
    state.report_ar = report

    ctx.trail.append(
        transaction_id=state.transaction_id,
        event=AuditEvent.REPORT_ISSUED,
        actor=state.actor,
        masked_prompt=state.masked_request,
        citations=state.citations,
        reasoning_trace=state.reasoning_trace,
        tool_invocations=state.tool_invocations,
        outcome=f"report:{source}",
        detail={
            "findings": [f.to_dict() for f in state.findings],
            "requires_approval": state.requires_approval,
            "report_source": source,
        },
    )
    return state


def approval_gate_node(state: AgentState, ctx: GraphContext) -> AgentState:
    """FR-4.1 - suspend the transaction and hand it to a Director.

    This is the interrupt point. The run stops here with state persisted; it
    resumes only when a human with approve authority decides.
    """
    if not state.requires_approval:
        state.status = TransactionStatus.COMPLETED
        return state

    state.status = TransactionStatus.PENDING_APPROVAL
    state.trace("عُلّقت المعاملة بانتظار اعتماد صاحب الصلاحية.")

    ctx.trail.append(
        transaction_id=state.transaction_id,
        event=AuditEvent.APPROVAL_REQUESTED,
        actor=state.actor,
        outcome=TransactionStatus.PENDING_APPROVAL.value,
        detail={"reasons": state.approval_reasons},
    )
    return state
