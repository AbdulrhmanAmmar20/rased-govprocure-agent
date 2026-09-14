"""verify_budget_ceiling (FR-3.1).

"التأكد من توفر ميزانية وعدم تجاوز السقف النظامي للشراء المباشر."

Two distinct controls share one tool because they fail together in practice
and a reviewer needs to see both at once:

* prior appropriation — is the money actually there (Article 20), and
* procurement route — is direct purchase even lawful at this amount
  (Article 34).

They are reported separately, though. A transaction can be fully funded and
still unlawful as a direct purchase, which is precisely the SRS section 6
case, and collapsing the two into a single pass/fail would hide it.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.rbac import Permission
from app.tools.backends import BudgetLedger, budget_ledger
from app.tools.registry import ToolSpec

BASIS_APPROPRIATION = "GTPL-ART-20"
BASIS_CEILING = "IR-ART-34"
BASIS_LIMITED_COMPETITION = "GTPL-ART-28"
BASIS_SPLITTING = "GTPL-ART-72"

ROUTE_DIRECT = "direct_purchase"
ROUTE_LIMITED = "limited_competition"
ROUTE_OPEN = "open_competition"

# Above this multiple of the ceiling, limited competition is no longer a
# proportionate route and the transaction belongs in an open tender.
_OPEN_COMPETITION_MULTIPLE = 5.0


def verify_budget_ceiling(
    department_id: str,
    amount: float,
    *,
    ledger: BudgetLedger | None = None,
) -> dict[str, Any]:
    """Check appropriation and the lawful procurement route for an amount.

    Args:
        department_id: Owning department, e.g. ``IT-DEPT``.
        amount: Contract value in SAR.
    """
    settings = get_settings()
    ledger = ledger or budget_ledger()
    ceiling = settings.direct_purchase_ceiling_sar

    amount = float(amount)
    record = ledger.get(department_id)

    findings: list[str] = []
    violations: list[str] = []

    # -- Control 1: prior appropriation (Article 20) -----------------------
    if record is None:
        budget_ok = False
        violations.append(
            f"لا يوجد اعتماد مالي مرصود للجهة '{department_id}' في قاعدة البيانات."
        )
        available = 0.0
    else:
        available = record.available_sar
        budget_ok = available >= amount
        if not budget_ok:
            violations.append(
                f"الاعتماد المالي المتاح ({available:,.2f} ريال) لا يغطي قيمة "
                f"التعاقد ({amount:,.2f} ريال)."
            )

    # -- Control 2: procurement route (Articles 34 / 28) -------------------
    within_ceiling = amount <= ceiling
    if within_ceiling:
        required_route = ROUTE_DIRECT
    elif amount <= ceiling * _OPEN_COMPETITION_MULTIPLE:
        required_route = ROUTE_LIMITED
    else:
        required_route = ROUTE_OPEN

    if not within_ceiling:
        violations.append(
            f"قيمة التعاقد ({amount:,.2f} ريال) تتجاوز السقف النظامي للشراء "
            f"المباشر ({ceiling:,.2f} ريال)."
        )
        route_ar = "منافسة محدودة" if required_route == ROUTE_LIMITED else "منافسة عامة"
        findings.append(
            f"يلزم تحويل مسار الشراء إلى {route_ar}، أو الحصول على موافقة "
            "استثنائية من صاحب الصلاحية."
        )

    # A value sitting just under the ceiling is the shape that purchase
    # splitting takes, so it is surfaced for human attention rather than
    # being treated as a clean pass.
    near_ceiling = within_ceiling and amount >= ceiling * 0.9
    if near_ceiling:
        findings.append(
            "القيمة قريبة من السقف النظامي؛ يُتحقق من عدم تجزئة المشتريات "
            "للتحايل على الحد المالي."
        )

    legal_basis = [BASIS_APPROPRIATION, BASIS_CEILING]
    if not within_ceiling:
        legal_basis.append(BASIS_LIMITED_COMPETITION)
    if near_ceiling:
        legal_basis.append(BASIS_SPLITTING)

    return {
        "department_id": department_id,
        "amount_sar": amount,
        "ceiling_sar": ceiling,
        "within_ceiling": within_ceiling,
        "budget_available_sar": available,
        "budget_sufficient": budget_ok,
        "required_route": required_route,
        "compliant": budget_ok and within_ceiling,
        "violations": violations,
        "findings": findings,
        "near_ceiling": near_ceiling,
        "legal_basis": legal_basis,
    }


SPEC = ToolSpec(
    name="verify_budget_ceiling",
    description=(
        "التحقق من توفر اعتماد مالي كافٍ لدى الجهة، ومن عدم تجاوز قيمة "
        "التعاقد للسقف النظامي للشراء المباشر، وتحديد مسار الشراء النظامي."
    ),
    required_permission=Permission.QUERY_REGULATIONS,
    parameters={
        "type": "object",
        "properties": {
            "department_id": {"type": "string", "description": "معرّف الإدارة صاحبة الطلب."},
            "amount": {"type": "number", "description": "قيمة التعاقد بالريال السعودي."},
        },
        "required": ["department_id", "amount"],
    },
    handler=verify_budget_ceiling,
)
