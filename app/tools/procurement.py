"""create_procurement_order (FR-3.1, FR-3.2).

This is the only tool in the catalogue that changes anything outside Rased,
and it is the one FR-3.2 names explicitly: the agent may not reach it unless
the accompanying token carries the Director role.

The handler re-runs the ceiling and appropriation checks itself rather than
trusting that the agent already did. The agent is a language model deciding
which tools to call in which order; it may skip a step, call this one first,
or be talked into doing so by the submitted text. Re-validating here means the
guarantee does not depend on the model's behaviour at all — the gateway
controls *who* may call, and the handler controls *what may be created*.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.core.rbac import Permission
from app.tools.budget import verify_budget_ceiling
from app.tools.registry import ToolSpec
from app.tools.vendor import check_vendor_eligibility

BASIS_AUTHORITY = "IR-ART-88"
BASIS_LOCAL_CONTENT = "IR-ART-96"

STATUS_DRAFT = "DRAFT"
STATUS_BLOCKED = "BLOCKED"

_orders: dict[str, dict[str, Any]] = {}


def create_procurement_order(
    vendor_id: str,
    amount: float,
    contract_terms: str = "",
    department_id: str = "",
) -> dict[str, Any]:
    """Create a draft purchase order, subject to a fresh compliance re-check.

    Args:
        vendor_id: The vendor's commercial registration number, unmasked.
        amount: Contract value in SAR.
        contract_terms: Free-text summary of scope and terms.
        department_id: Owning department, used to re-check appropriation.

    Returns:
        A DRAFT order, or a BLOCKED result listing what prevented it. The draft
        is never an award: issuing it still requires the Director's explicit
        approval through the human-in-the-loop route (FR-4.2).
    """
    settings = get_settings()
    amount = float(amount)

    blockers: list[str] = []

    eligibility = check_vendor_eligibility(vendor_id)
    if not eligibility["eligible"]:
        blockers.extend(eligibility["blocking_issues"])

    budget = verify_budget_ceiling(department_id or "UNKNOWN", amount)
    blockers.extend(budget["violations"])

    local_content = float(eligibility.get("local_content_ratio", 0.0))
    if eligibility["found"] and local_content < settings.min_local_content_ratio:
        blockers.append(
            f"نسبة المحتوى المحلي للمورد ({local_content:.0%}) أقل من الحد "
            f"المطلوب ({settings.min_local_content_ratio:.0%})."
        )

    legal_basis = [BASIS_AUTHORITY, BASIS_LOCAL_CONTENT, *budget["legal_basis"]]

    if blockers:
        return {
            "status": STATUS_BLOCKED,
            "order_id": None,
            "vendor_id": vendor_id,
            "amount_sar": amount,
            "blockers": blockers,
            "legal_basis": sorted(set(legal_basis)),
        }

    order_id = f"PO-{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
    order = {
        "status": STATUS_DRAFT,
        "order_id": order_id,
        "vendor_id": vendor_id,
        "vendor_name": eligibility.get("legal_name", ""),
        "amount_sar": amount,
        "department_id": department_id,
        "contract_terms": contract_terms,
        "local_content_ratio": local_content,
        "required_route": budget["required_route"],
        "created_at": datetime.now(UTC).isoformat(),
        "blockers": [],
        "legal_basis": sorted(set(legal_basis)),
        "note": "مسودة أمر شراء. لا تُعد ترسية ولا تُنفذ إلا بعد اعتماد صاحب الصلاحية.",
    }
    _orders[order_id] = order
    return order


def get_order(order_id: str) -> dict[str, Any] | None:
    return _orders.get(order_id)


SPEC = ToolSpec(
    name="create_procurement_order",
    description=(
        "إنشاء مسودة أمر شراء بعد إعادة التحقق من أهلية المورد، وتوفر "
        "الاعتماد المالي، والسقف النظامي، ونسبة المحتوى المحلي. "
        "متاحة لصاحب الصلاحية فقط."
    ),
    required_permission=Permission.CREATE_PROCUREMENT_ORDER,
    parameters={
        "type": "object",
        "properties": {
            "vendor_id": {"type": "string", "description": "رقم السجل التجاري للمورد."},
            "amount": {"type": "number", "description": "قيمة التعاقد بالريال السعودي."},
            "contract_terms": {"type": "string", "description": "ملخص نطاق العمل والشروط."},
            "department_id": {"type": "string", "description": "معرّف الإدارة صاحبة الطلب."},
        },
        "required": ["vendor_id", "amount"],
    },
    handler=create_procurement_order,
    mutating=True,
)
