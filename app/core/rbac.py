"""Role-based access control (SRS section 2, FR-3.2).

The matrix below is the authoritative encoding of the SRS role table. It is
data, not scattered ``if`` statements, so a Compliance Auditor can diff it
against the organisation's delegation-of-authority document directly.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The four roles defined in SRS section 2."""

    SPECIALIST = "specialist"  # أخصائي مشتريات
    AUDITOR = "auditor"  # مدقق نظامي
    DIRECTOR = "director"  # صاحب صلاحية / مدير إدارة
    ADMIN = "admin"  # مدير النظام

    @property
    def arabic_title(self) -> str:
        return _ARABIC_TITLES[self]


_ARABIC_TITLES: dict[Role, str] = {
    Role.SPECIALIST: "أخصائي مشتريات",
    Role.AUDITOR: "مدقق نظامي",
    Role.DIRECTOR: "صاحب صلاحية / مدير إدارة",
    Role.ADMIN: "مدير النظام",
}


class Permission(StrEnum):
    """Atomic capabilities. Tools and routes are guarded by these, never by role."""

    # Case preparation
    SUBMIT_PROCUREMENT_REQUEST = "procurement:submit"
    QUERY_REGULATIONS = "regulations:query"
    DRAFT_REVIEW_MEMO = "memo:draft"

    # Oversight
    READ_AUDIT_LOG = "audit:read"
    VERIFY_AGENT_CITATIONS = "agent:verify_citations"

    # Financial authority
    APPROVE_TRANSACTION = "transaction:approve"
    CREATE_PROCUREMENT_ORDER = "order:create"
    ISSUE_DIRECT_PURCHASE = "order:issue_direct"

    # Platform operations
    MANAGE_INFRASTRUCTURE = "infra:manage"
    MANAGE_VECTOR_DB = "vectordb:manage"
    READ_METRICS = "metrics:read"

    # Data exposure — deliberately separate from every other permission.
    REVEAL_MASKED_DATA = "pii:reveal"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    # "رفع العروض، استعلام الوكيل عن مطابقة اللائحة، إنشاء مسودات مذكرات الفحص.
    #  لا يملك صلاحية اعتماد الميزانية أو إصدار أوامر الصرف."
    Role.SPECIALIST: frozenset(
        {
            Permission.SUBMIT_PROCUREMENT_REQUEST,
            Permission.QUERY_REGULATIONS,
            Permission.DRAFT_REVIEW_MEMO,
            Permission.REVEAL_MASKED_DATA,
        }
    ),
    # "استعراض سجلات التدقيق، التحقق من تفسيرات الوكيل للوائح."
    Role.AUDITOR: frozenset(
        {
            Permission.READ_AUDIT_LOG,
            Permission.VERIFY_AGENT_CITATIONS,
            Permission.QUERY_REGULATIONS,
            Permission.REVEAL_MASKED_DATA,
        }
    ),
    # "اعتماد مذكرات الترسية النهائية، الموافقة على المعاملات المعلقة،
    #  وإصدار أوامر الشراء المباشر."
    Role.DIRECTOR: frozenset(
        {
            Permission.SUBMIT_PROCUREMENT_REQUEST,
            Permission.QUERY_REGULATIONS,
            Permission.DRAFT_REVIEW_MEMO,
            Permission.APPROVE_TRANSACTION,
            Permission.CREATE_PROCUREMENT_ORDER,
            Permission.ISSUE_DIRECT_PURCHASE,
            Permission.READ_AUDIT_LOG,
            Permission.REVEAL_MASKED_DATA,
        }
    ),
    # "إدارة إعدادات البنية التحتية، تحديث قواعد بيانات اللوائح المتجهة،
    #  ومراقبة مؤشرات الأداء والأمان."
    #
    # Note the omission of REVEAL_MASKED_DATA: the platform engineer operates
    # the system but has no business reading citizens' identity numbers or a
    # vendor's banking details. Separation of duty, per NCA ECC-1:2018 2-2-3.
    Role.ADMIN: frozenset(
        {
            Permission.MANAGE_INFRASTRUCTURE,
            Permission.MANAGE_VECTOR_DB,
            Permission.READ_METRICS,
            Permission.READ_AUDIT_LOG,
        }
    ),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)


def describe_matrix() -> dict[str, list[str]]:
    """Human-readable dump of the matrix, exposed to auditors over the API."""
    return {role.value: sorted(p.value for p in perms) for role, perms in ROLE_PERMISSIONS.items()}
