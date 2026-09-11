"""Deterministic compliance evaluation.

This module, not the language model, decides whether a transaction complies.

That division is the central architectural choice in Rased. A 7B-14B model is
good at reading an Arabic request and writing a readable Arabic report; it is
not something an audit function should rely on to decide, reproducibly and
identically every time, whether 120,000 exceeds 100,000. Compliance verdicts
are arithmetic and lookups over tool output, so they are computed here, and
the model's role is reduced to narrating a conclusion already reached.

The practical consequences are worth stating: the same transaction always
produces the same verdict, the verdict survives a model upgrade unchanged, an
auditor can reproduce it without inference hardware, and a prompt-injection
attempt in the submitted text cannot alter it - at worst it corrupts the prose
around a verdict it cannot reach.
"""

from __future__ import annotations

from app.agent.state import AgentState, Finding, Severity
from app.config import get_settings
from app.tools.budget import (
    BASIS_APPROPRIATION,
    BASIS_CEILING,
    BASIS_LIMITED_COMPETITION,
    BASIS_SPLITTING,
)
from app.tools.procurement import BASIS_AUTHORITY, BASIS_LOCAL_CONTENT
from app.tools.vendor import LEGAL_BASIS as BASIS_ELIGIBILITY

CODE_MISSING_AMOUNT = "MISSING_AMOUNT"
CODE_CEILING_EXCEEDED = "DIRECT_PURCHASE_CEILING_EXCEEDED"
CODE_BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
CODE_VENDOR_INELIGIBLE = "VENDOR_INELIGIBLE"
CODE_VENDOR_WARNING = "VENDOR_WARNING"
CODE_LOCAL_CONTENT_LOW = "LOCAL_CONTENT_BELOW_MINIMUM"
CODE_SPLITTING_RISK = "SPLITTING_RISK"
CODE_AUTHORITY_REQUIRED = "APPROVER_AUTHORITY_REQUIRED"
CODE_COMPLIANT = "COMPLIANT"


def _effective_local_content(state: AgentState, vendor: dict) -> float | None:
    """Ratio stated in the request, else the vendor's registered ratio."""
    if state.facts.local_content_ratio is not None:
        return state.facts.local_content_ratio
    if vendor:
        value = vendor.get("local_content_ratio")
        return float(value) if value is not None else None
    return None


def evaluate(state: AgentState) -> list[Finding]:
    """Produce every finding implied by the facts and tool results."""
    settings = get_settings()
    findings: list[Finding] = []

    budget = state.tool_results.get("verify_budget_ceiling", {})
    vendor = state.tool_results.get("check_vendor_eligibility", {})

    if state.facts.amount_sar is None:
        findings.append(
            Finding(
                code=CODE_MISSING_AMOUNT,
                severity=Severity.WARNING,
                message_ar=(
                    "تعذر استخراج قيمة التعاقد من نص الطلب، ولا يمكن التحقق من "
                    "السقف النظامي دون قيمة محددة."
                ),
                legal_basis=(BASIS_CEILING,),
            )
        )

    if budget and not budget.get("within_ceiling", True):
        findings.append(
            Finding(
                code=CODE_CEILING_EXCEEDED,
                severity=Severity.VIOLATION,
                message_ar=(
                    f"قيمة التعاقد ({budget['amount_sar']:,.2f} ريال) تتجاوز السقف "
                    f"النظامي للشراء المباشر ({budget['ceiling_sar']:,.2f} ريال)."
                ),
                legal_basis=(BASIS_CEILING, BASIS_LIMITED_COMPETITION),
                detail={
                    "amount_sar": budget["amount_sar"],
                    "ceiling_sar": budget["ceiling_sar"],
                    "required_route": budget.get("required_route"),
                },
            )
        )

    if budget and not budget.get("budget_sufficient", True):
        findings.append(
            Finding(
                code=CODE_BUDGET_INSUFFICIENT,
                severity=Severity.VIOLATION,
                message_ar=(
                    f"الاعتماد المالي المتاح ({budget.get('budget_available_sar', 0):,.2f} "
                    "ريال) لا يغطي قيمة التعاقد؛ ولا يجوز التعاقد دون اعتماد مسبق كافٍ."
                ),
                legal_basis=(BASIS_APPROPRIATION,),
                detail={"available_sar": budget.get("budget_available_sar")},
            )
        )

    if budget and budget.get("near_ceiling"):
        findings.append(
            Finding(
                code=CODE_SPLITTING_RISK,
                severity=Severity.WARNING,
                message_ar=(
                    "قيمة التعاقد قريبة جداً من السقف النظامي؛ يلزم التحقق من عدم "
                    "تجزئة المشتريات بقصد تجنب إجراءات المنافسة."
                ),
                legal_basis=(BASIS_SPLITTING,),
            )
        )
    return _evaluate_vendor_and_content(state, findings, budget, vendor, settings)


def _evaluate_vendor_and_content(
    state: AgentState,
    findings: list[Finding],
    budget: dict,
    vendor: dict,
    settings,
) -> list[Finding]:
    """Second half of the evaluation: eligibility, local content, authority."""
    if vendor:
        if not vendor.get("eligible", True):
            findings.append(
                Finding(
                    code=CODE_VENDOR_INELIGIBLE,
                    severity=Severity.VIOLATION,
                    message_ar="المورد غير مستوفٍ للشروط النظامية: "
                    + "؛ ".join(vendor.get("blocking_issues", [])),
                    legal_basis=(BASIS_ELIGIBILITY,),
                    detail={"checks": vendor.get("checks", {})},
                )
            )
        for warning in vendor.get("warnings", []):
            findings.append(
                Finding(
                    code=CODE_VENDOR_WARNING,
                    severity=Severity.WARNING,
                    message_ar=warning,
                    legal_basis=(BASIS_ELIGIBILITY,),
                )
            )

    ratio = _effective_local_content(state, vendor)
    if ratio is not None and ratio < settings.min_local_content_ratio:
        findings.append(
            Finding(
                code=CODE_LOCAL_CONTENT_LOW,
                severity=Severity.VIOLATION,
                message_ar=(
                    f"نسبة المحتوى المحلي ({ratio:.0%}) أقل من الحد الأدنى المطلوب "
                    f"({settings.min_local_content_ratio:.0%})."
                ),
                legal_basis=(BASIS_LOCAL_CONTENT,),
                detail={"ratio": ratio, "minimum": settings.min_local_content_ratio},
            )
        )

    if any(f.severity is Severity.VIOLATION for f in findings):
        findings.append(
            Finding(
                code=CODE_AUTHORITY_REQUIRED,
                severity=Severity.INFO,
                message_ar=(
                    "لا يجوز الاستمرار في المعاملة إلا بعد عرضها على صاحب الصلاحية "
                    "واعتمادها، أو تعديل مسار الشراء وفق النظام."
                ),
                legal_basis=(BASIS_AUTHORITY,),
            )
        )
    elif not findings:
        findings.append(
            Finding(
                code=CODE_COMPLIANT,
                severity=Severity.INFO,
                message_ar="لم تُرصد مخالفات نظامية بناءً على الفحوصات المتاحة.",
                legal_basis=(BASIS_CEILING, BASIS_ELIGIBILITY),
            )
        )

    return findings


def requires_human_approval(state: AgentState) -> tuple[bool, list[str]]:
    """FR-4.1 - decide whether the transaction must be suspended.

    Two triggers are named in the SRS: the amount exceeding the ceiling, and
    local content below the required floor. Vendor ineligibility and an
    insufficient appropriation are added because they are violations of the
    same weight, and stopping for two while letting the others pass unreviewed
    would be incoherent.
    """
    settings = get_settings()
    reasons: list[str] = []

    budget = state.tool_results.get("verify_budget_ceiling", {})
    vendor = state.tool_results.get("check_vendor_eligibility", {})

    if budget and not budget.get("within_ceiling", True):
        reasons.append(
            f"تجاوز السقف النظامي للشراء المباشر ({budget['ceiling_sar']:,.0f} ريال)."
        )
    if budget and not budget.get("budget_sufficient", True):
        reasons.append("عدم كفاية الاعتماد المالي المرصود.")

    ratio = _effective_local_content(state, vendor)
    if ratio is not None and ratio < settings.min_local_content_ratio:
        reasons.append(
            f"نسبة المحتوى المحلي أقل من الحد المطلوب ({settings.min_local_content_ratio:.0%})."
        )

    if vendor and not vendor.get("eligible", True):
        reasons.append("عدم استيفاء المورد للشروط النظامية.")

    return bool(reasons), reasons
