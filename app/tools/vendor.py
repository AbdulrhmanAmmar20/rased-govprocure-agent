"""check_vendor_eligibility (FR-3.1).

"فحص صلاحية السجل وشهادات السعودة والزكاة."

The tool returns a determination plus the reasons behind it. Returning a bare
boolean would be worse than useless here: the agent has to explain to a
reviewer *why* a vendor was excluded, and a reviewer has to be able to check
that reason against the article it rests on.
"""

from __future__ import annotations

from typing import Any

from app.core.rbac import Permission
from app.tools.backends import VendorDirectory, vendor_directory
from app.tools.registry import ToolSpec

# The article that makes these certificates a condition of bidding.
LEGAL_BASIS = "GTPL-ART-10"


def check_vendor_eligibility(
    cr_number: str,
    *,
    directory: VendorDirectory | None = None,
) -> dict[str, Any]:
    """Assess whether a vendor may be awarded work.

    Args:
        cr_number: The vendor's commercial registration number, unmasked.

    Returns:
        A determination with blocking issues, warnings and the legal basis.
    """
    directory = directory or vendor_directory()
    record = directory.get(cr_number)

    if record is None:
        return {
            "cr_number": cr_number,
            "found": False,
            "eligible": False,
            "blocking_issues": ["لا يوجد سجل تجاري مطابق في قاعدة بيانات الموردين."],
            "warnings": [],
            "checks": {},
            "legal_basis": LEGAL_BASIS,
        }

    blocking: list[str] = []
    warnings: list[str] = []

    # Debarment is checked first and is absolute — a debarred vendor with
    # immaculate paperwork is still debarred.
    if record.debarred:
        blocking.append(
            f"المورد مدرج ضمن قائمة المتعاقدين المستبعدين. السبب: {record.debarment_reason}"
        )

    if not record.cr_active:
        blocking.append("السجل التجاري غير ساري المفعول.")

    for certificate in (record.zakat, record.gosi, record.saudization):
        if not certificate.valid:
            blocking.append(f"{certificate.name} غير سارية.")

    if record.saudization_band in {"red", "yellow"}:
        message = f"نطاق السعودة الحالي للمنشأة: {record.saudization_band}."
        if record.saudization_band == "red":
            blocking.append(message + " النطاق الأحمر يمنع التعاقد.")
        else:
            warnings.append(message + " يتطلب مراجعة قبل الترسية.")

    return {
        "cr_number": record.cr_number,
        "found": True,
        "legal_name": record.legal_name,
        "activity": record.activity,
        "eligible": not blocking,
        "blocking_issues": blocking,
        "warnings": warnings,
        "checks": {
            "cr_active": record.cr_active,
            "zakat_valid": record.zakat.valid,
            "gosi_valid": record.gosi.valid,
            "saudization_valid": record.saudization.valid,
            "saudization_band": record.saudization_band,
            "debarred": record.debarred,
        },
        "local_content_ratio": record.local_content_ratio,
        "legal_basis": LEGAL_BASIS,
    }


SPEC = ToolSpec(
    name="check_vendor_eligibility",
    description=(
        "فحص أهلية المورد: سريان السجل التجاري، وشهادات الزكاة والدخل "
        "والتأمينات الاجتماعية والالتزام بالسعودة، ونطاق السعودة، "
        "وإدراجه ضمن المستبعدين."
    ),
    required_permission=Permission.QUERY_REGULATIONS,
    parameters={
        "type": "object",
        "properties": {
            "cr_number": {
                "type": "string",
                "description": "رقم السجل التجاري للمورد (10 أرقام).",
            }
        },
        "required": ["cr_number"],
    },
    handler=check_vendor_eligibility,
)
