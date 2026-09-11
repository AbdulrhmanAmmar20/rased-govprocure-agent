"""Report generation (FR-2.2, NFR-2.2, NFR-3.1).

The verdict is already decided by app.agent.rules before this runs. What is
left is turning findings into something a reviewer reads, which is the part a
language model is actually good at.

Three outcomes are possible and all three are acceptable:

* the model writes the report and its citations check out - used as-is;
* the model writes the report but cites an article that was never retrieved -
  discarded, and the deterministic template is used instead (FR-2.3);
* no sovereign endpoint is reachable - the deterministic template is used.

The template is not a degraded mode. It renders every finding with its legal
basis and is always correct; it simply reads like a form rather than prose.
Being able to fall back to it is what keeps an inference outage from stopping
procurement review altogether.
"""

from __future__ import annotations

from app.agent.llm import SovereignLLM
from app.agent.prompts import SYSTEM_PROMPT_AR, assert_prompt_is_masked, build_user_prompt
from app.agent.state import AgentState, Severity
from app.core.exceptions import MaskingError, ResidencyViolationError
from app.core.logging import get_logger
from app.rag.retriever import RetrievalResult, find_unsupported_citations

logger = get_logger("rased.report")

DISCLAIMER_SEED = (
    "\n\n---\n"
    "تنبيه: قاعدة اللوائح المستخدمة في هذا التقرير ملخصات استرشادية للتطوير "
    "وليست النص الرسمي المعتمد. لا يُعتمد هذا التقرير نظامياً قبل استبدالها."
)


def _facts_block(state: AgentState) -> str:
    facts = state.facts
    amount = f"{facts.amount_sar:,.2f} ريال" if facts.amount_sar is not None else "غير محدد"
    lines = [
        f"- قيمة التعاقد: {amount}",
        f"- الإدارة صاحبة الطلب: {facts.department_id or 'غير محدد'}",
        "- المورد: حقل محجوب (يُشار إليه بالمعرّف الوارد في نص الطلب)",
    ]
    if facts.local_content_ratio is not None:
        lines.append(f"- نسبة المحتوى المحلي المذكورة: {facts.local_content_ratio:.0%}")
    return "\n".join(lines)


def _tool_block(state: AgentState) -> str:
    if not state.tool_results:
        return ""
    lines: list[str] = []
    for name, result in state.tool_results.items():
        lines.append(f"- {name}:")
        for key, value in result.items():
            if key in {"legal_basis", "checks"} or isinstance(value, (list, dict)):
                continue
            lines.append(f"    {key} = {value}")
    return "\n".join(lines)


def render_template(state: AgentState) -> str:
    """Deterministic Arabic report. Always correct, never eloquent."""
    violations = [f for f in state.findings if f.severity is Severity.VIOLATION]
    warnings = [f for f in state.findings if f.severity is Severity.WARNING]
    infos = [f for f in state.findings if f.severity is Severity.INFO]

    if violations:
        headline = f"الخلاصة: تم رصد {len(violations)} مخالفة نظامية في هذه المعاملة."
    elif warnings:
        headline = "الخلاصة: لم تُرصد مخالفات، مع وجود ملاحظات تستوجب المراجعة."
    else:
        headline = "الخلاصة: المعاملة مطابقة للفحوصات النظامية المتاحة."

    parts = [headline, "", "الملاحظات:"]
    index = 1
    for finding in [*violations, *warnings, *infos]:
        basis = "، ".join(finding.legal_basis) if finding.legal_basis else "بدون سند"
        parts.append(f"{index}. [{finding.severity.arabic}] {finding.message_ar} ({basis})")
        index += 1

    parts.append("")
    parts.append("التوصية:")
    if state.requires_approval:
        parts.append(
            "تُعلَّق المعاملة وتُحال إلى صاحب الصلاحية لاعتمادها أو رفضها، "
            "للأسباب التالية:"
        )
        parts.extend(f"  - {reason}" for reason in state.approval_reasons)
    elif violations:
        parts.append("يلزم تصحيح المخالفات المذكورة قبل استكمال الإجراء.")
    else:
        parts.append("يمكن استكمال الإجراء وفق المسار النظامي المحدد.")

    return "\n".join(parts)


def generate_report(
    state: AgentState,
    retrieval: RetrievalResult,
    *,
    llm: SovereignLLM | None = None,
) -> tuple[str, str]:
    """Return ``(report_text, source)`` where source is 'model' or 'template'."""
    template = render_template(state)

    try:
        client = llm or SovereignLLM()
    except ResidencyViolationError:
        logger.error("inference endpoint failed the residency check; using template")
        state.trace("محرك الاستدلال غير مطابق لمتطلب سيادة البيانات؛ استُخدم التقرير النمطي.")
        return template, "template"

    if not client.is_available():
        state.trace("محرك الاستدلال غير متاح؛ استُخدم التقرير النمطي.")
        return template, "template"

    prompt = build_user_prompt(
        context=retrieval.context_block(),
        facts=_facts_block(state),
        tool_results=_tool_block(state),
        masked_request=state.masked_request,
    )

    try:
        assert_prompt_is_masked(prompt)
    except MaskingError:
        # Never silently downgrade a masking failure into a template report
        # without saying so: this is the one fallback that indicates a defect
        # rather than an outage.
        state.trace("أُوقف إرسال الطلب لاحتوائه على بيانات غير محجوبة؛ استُخدم التقرير النمطي.")
        return template, "template"

    try:
        response = client.complete(SYSTEM_PROMPT_AR, prompt)
    except Exception:  # noqa: BLE001
        logger.exception("inference call failed; using template")
        state.trace("فشل استدعاء محرك الاستدلال؛ استُخدم التقرير النمطي.")
        return template, "template"

    unsupported = find_unsupported_citations(response.text, retrieval)
    if unsupported:
        # FR-2.3 - a fabricated article number must not reach a reviewer
        # wearing the authority of a citation.
        logger.warning(
            "model cited articles absent from the retrieved set; report discarded",
            extra={"unsupported": sorted(unsupported)},
        )
        state.trace(
            "رُفض تقرير النموذج لاستشهاده بمواد غير واردة في المراجع المسترجعة: "
            + "، ".join(sorted(unsupported))
        )
        return template, "template"

    state.trace(
        f"وُلّد التقرير عبر النموذج {response.model} خلال {response.latency_ms:.0f} مللي ثانية."
    )
    return response.text, "model"
