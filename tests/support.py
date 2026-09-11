"""Shared test helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.audit.trail import AuditTrail
from app.config import get_settings
from app.core.rbac import Role
from app.core.security import Principal, issue_token, verify_token

SCENARIO_REQUEST = (
    "أرغب في ترسية شراء مباشر لكاميرات مراقبة على مؤسسة الأفق "
    "سجل تجاري 1010998877 بمبلغ 120,000 ريال وفق نظام المنافسات."
)

SCENARIO_CR = "1010998877"


def principal(role: Role, subject: str = "emp-test", department: str = "IT-DEPT") -> Principal:
    """A verified principal, minted through the real token path.

    Constructing Principal directly would skip token verification and let a
    test pass against a system whose auth is broken.
    """
    return verify_token(issue_token(subject, role, department))


def temp_trail() -> AuditTrail:
    """An audit trail in a throwaway directory."""
    return AuditTrail(Path(tempfile.mkdtemp(prefix="rased-audit-")) / "audit.jsonl")


def valid_saudi_id() -> str:
    """A checksum-valid National ID for masking tests.

    Derived rather than hard-coded, so the fixture cannot silently drift from
    the validator it is meant to exercise.
    """
    from app.pii.validators import is_valid_saudi_id

    for tail in range(100):
        candidate = f"10987654{tail:02d}"
        if is_valid_saudi_id(candidate):
            return candidate
    raise AssertionError("no valid identity number could be derived")


def reset_settings_cache() -> None:
    get_settings.cache_clear()
