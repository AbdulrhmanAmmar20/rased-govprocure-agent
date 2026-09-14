"""Data backends behind the tools.

In a real deployment these read from the entity's ERP, from منصة اعتماد, and
from the Zakat, GOSI and Saudisation certificate services. The in-memory
fixtures here stand in for those integrations so the whole compliance path can
be exercised end to end without them.

The seam is deliberate: tools depend on the ``VendorDirectory`` and
``BudgetLedger`` interfaces, not on where the data came from, so substituting
the live integrations is a constructor change and nothing else.

Every lookup here is by CR number - an unmasked value. That is correct and
worth stating plainly: the tool layer runs inside the trust boundary and needs
real identifiers to do its job. It is the *model* that must never see them,
which is why unmasking happens between the agent and the gateway rather than
at the API edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Certificate:
    """A compliance certificate with an expiry."""

    name: str
    valid: bool
    expires_on: date | None = None

    def status_ar(self) -> str:
        return "سارية" if self.valid else "منتهية أو غير متوفرة"


@dataclass(frozen=True, slots=True)
class VendorRecord:
    cr_number: str
    legal_name: str
    cr_active: bool
    activity: str
    zakat: Certificate
    gosi: Certificate
    saudization: Certificate
    saudization_band: str  # platinum / green / yellow / red
    local_content_ratio: float
    debarred: bool = False
    debarment_reason: str = ""


@dataclass(frozen=True, slots=True)
class BudgetRecord:
    department_id: str
    fiscal_year: int
    appropriation_sar: float
    committed_sar: float

    @property
    def available_sar(self) -> float:
        return self.appropriation_sar - self.committed_sar


class VendorDirectory:
    """Vendor eligibility lookup, keyed by commercial registration number."""

    def __init__(self, records: dict[str, VendorRecord] | None = None) -> None:
        self._records = records if records is not None else _seed_vendors()

    def get(self, cr_number: str) -> VendorRecord | None:
        return self._records.get(cr_number.strip())


class BudgetLedger:
    """Departmental appropriation lookup."""

    def __init__(self, records: dict[str, BudgetRecord] | None = None) -> None:
        self._records = records if records is not None else _seed_budgets()

    def get(self, department_id: str) -> BudgetRecord | None:
        return self._records.get(department_id.strip().upper())


def _seed_vendors() -> dict[str, VendorRecord]:
    """Development fixtures, including the vendor from the SRS scenario."""
    return {
        # SRS section 6 - مؤسسة الأفق. Fully eligible, so that the scenario
        # fails on the financial ceiling alone and not on a second defect.
        "1010998877": VendorRecord(
            cr_number="1010998877",
            legal_name="مؤسسة الأفق للتجارة",
            cr_active=True,
            activity="توريد وتركيب أنظمة المراقبة الأمنية",
            zakat=Certificate("شهادة الزكاة والدخل", True, date(2027, 3, 31)),
            gosi=Certificate("شهادة التأمينات الاجتماعية", True, date(2026, 12, 31)),
            saudization=Certificate("شهادة الالتزام بالسعودة", True, date(2026, 11, 30)),
            saudization_band="green",
            local_content_ratio=0.42,
        ),
        # Expired certificates - exercises the eligibility failure path.
        "4030112233": VendorRecord(
            cr_number="4030112233",
            legal_name="شركة البناء الحديث",
            cr_active=True,
            activity="مقاولات عامة",
            zakat=Certificate("شهادة الزكاة والدخل", False, date(2025, 12, 31)),
            gosi=Certificate("شهادة التأمينات الاجتماعية", True, date(2026, 10, 31)),
            saudization=Certificate("شهادة الالتزام بالسعودة", False, None),
            saudization_band="red",
            local_content_ratio=0.18,
        ),
        # Debarred - must fail regardless of every other check.
        "2050445566": VendorRecord(
            cr_number="2050445566",
            legal_name="مؤسسة الشرق للتوريدات",
            cr_active=False,
            activity="توريدات عامة",
            zakat=Certificate("شهادة الزكاة والدخل", True, date(2026, 6, 30)),
            gosi=Certificate("شهادة التأمينات الاجتماعية", True, date(2026, 6, 30)),
            saudization=Certificate("شهادة الالتزام بالسعودة", True, date(2026, 6, 30)),
            saudization_band="yellow",
            local_content_ratio=0.35,
            debarred=True,
            debarment_reason="إخلال بالتزامات تعاقدية سابقة",
        ),
    }


def _seed_budgets() -> dict[str, BudgetRecord]:
    return {
        "IT-DEPT": BudgetRecord("IT-DEPT", 2026, 2_400_000.0, 1_850_000.0),
        "SEC-DEPT": BudgetRecord("SEC-DEPT", 2026, 900_000.0, 880_000.0),
        "OPS-DEPT": BudgetRecord("OPS-DEPT", 2026, 5_000_000.0, 1_000_000.0),
    }


_vendors = VendorDirectory()
_budgets = BudgetLedger()


def vendor_directory() -> VendorDirectory:
    return _vendors


def budget_ledger() -> BudgetLedger:
    return _budgets
