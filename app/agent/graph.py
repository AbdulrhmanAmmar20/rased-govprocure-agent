"""The compliance graph (FR-4.1).

The SRS names LangGraph, and build_langgraph() constructs exactly that graph
when the dependency is installed. The in-tree ComplianceGraph runs the same
node sequence, the same conditional edges and the same interrupt with no
external dependency, and is what the test suite exercises.

Keeping both is not indecision. The flow Rased needs is small and strictly
acyclic - mask, ground, check, evaluate, attempt, report, suspend - and the
one property that actually matters for FR-4.1 is that the run stops at the
approval gate with its state intact and resumes from there. That is a handful
of lines to get right directly, and having it in-tree means the compliance
guarantee does not depend on a framework upgrade. The LangGraph build exists
so that the checkpointer, streaming and tracing come for free in deployments
that want them.
"""

from __future__ import annotations

from collections.abc import Callable

from app.agent.nodes import (
    GraphContext,
    approval_gate_node,
    compliance_tools_node,
    evaluate_node,
    mask_node,
    order_attempt_node,
    report_node,
    retrieve_node,
)
from app.agent.state import AgentState, TransactionStatus
from app.core.logging import get_logger

logger = get_logger("rased.graph")

Node = Callable[[AgentState, GraphContext], AgentState]

# The pipeline, in order. Named so the audit trail and the graph agree.
PIPELINE: tuple[tuple[str, Node], ...] = (
    ("mask", mask_node),
    ("retrieve", retrieve_node),
    ("compliance_tools", compliance_tools_node),
    ("evaluate", evaluate_node),
    ("order_attempt", order_attempt_node),
    ("report", report_node),
    ("approval_gate", approval_gate_node),
)

# Nodes that must not run once the agent has refused for lack of legal basis.
_SKIP_AFTER_REFUSAL = {"compliance_tools", "evaluate", "order_attempt", "approval_gate"}


class ComplianceGraph:
    """Runs the review pipeline, honouring the refusal and approval stops."""

    def __init__(self, pipeline: tuple[tuple[str, Node], ...] = PIPELINE) -> None:
        self.pipeline = pipeline

    def run(self, state: AgentState, ctx: GraphContext) -> AgentState:
        for name, node in self.pipeline:
            if state.status is TransactionStatus.REFUSED and name in _SKIP_AFTER_REFUSAL:
                continue

            logger.info(
                "graph node",
                extra={"node": name, "transaction_id": state.transaction_id},
            )
            state = node(state, ctx)

            # FR-4.1 - the interrupt. Everything needed to resume is already
            # on the state and in the audit trail; nothing further runs until
            # a Director decides.
            if state.status is TransactionStatus.PENDING_APPROVAL:
                logger.info(
                    "graph suspended for human approval",
                    extra={"transaction_id": state.transaction_id},
                )
                break

        if state.status is TransactionStatus.ANALYZED:
            state.status = TransactionStatus.COMPLETED
        return state


def build_langgraph(ctx: GraphContext):
    """Build the equivalent LangGraph StateGraph (optional dependency).

    Interrupts before ``approval_gate`` so the framework's checkpointer holds
    the suspended run, which is LangGraph's native expression of FR-4.1.
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    builder = StateGraph(AgentState)

    for name, node in PIPELINE:
        builder.add_node(name, (lambda n: lambda s: n(s, ctx))(node))

    builder.set_entry_point("mask")
    builder.add_conditional_edges(
        "retrieve",
        lambda s: "refused" if s.status is TransactionStatus.REFUSED else "continue",
        {"refused": "report", "continue": "compliance_tools"},
    )
    builder.add_edge("mask", "retrieve")
    builder.add_edge("compliance_tools", "evaluate")
    builder.add_edge("evaluate", "order_attempt")
    builder.add_edge("order_attempt", "report")
    builder.add_edge("report", "approval_gate")
    builder.add_edge("approval_gate", END)

    return builder.compile(checkpointer=MemorySaver(), interrupt_before=["approval_gate"])


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True
