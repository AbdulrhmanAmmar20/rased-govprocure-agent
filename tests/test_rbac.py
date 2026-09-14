"""SRS section 2 and FR-3.2 - the role matrix and token handling."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.rbac import Permission, Role, describe_matrix, has_permission
from app.core.security import issue_token, verify_token
from tests.support import principal


class TestRoleMatrix(unittest.TestCase):
    def test_specialist_cannot_create_orders(self) -> None:
        """SRS: 'لا يملك صلاحية اعتماد الميزانية أو إصدار أوامر الصرف'."""
        self.assertFalse(
            has_permission(Role.SPECIALIST, Permission.CREATE_PROCUREMENT_ORDER)
        )
        self.assertFalse(has_permission(Role.SPECIALIST, Permission.APPROVE_TRANSACTION))

    def test_specialist_can_prepare_cases(self) -> None:
        for permission in (
            Permission.SUBMIT_PROCUREMENT_REQUEST,
            Permission.QUERY_REGULATIONS,
            Permission.DRAFT_REVIEW_MEMO,
        ):
            self.assertTrue(has_permission(Role.SPECIALIST, permission))

    def test_director_holds_financial_authority(self) -> None:
        for permission in (
            Permission.APPROVE_TRANSACTION,
            Permission.CREATE_PROCUREMENT_ORDER,
            Permission.ISSUE_DIRECT_PURCHASE,
        ):
            self.assertTrue(has_permission(Role.DIRECTOR, permission))

    def test_auditor_reads_but_cannot_act(self) -> None:
        self.assertTrue(has_permission(Role.AUDITOR, Permission.READ_AUDIT_LOG))
        self.assertFalse(has_permission(Role.AUDITOR, Permission.APPROVE_TRANSACTION))
        self.assertFalse(has_permission(Role.AUDITOR, Permission.CREATE_PROCUREMENT_ORDER))

    def test_admin_has_no_window_onto_personal_data(self) -> None:
        """Separation of duty - the platform engineer operates, never reads."""
        self.assertTrue(has_permission(Role.ADMIN, Permission.MANAGE_VECTOR_DB))
        self.assertFalse(has_permission(Role.ADMIN, Permission.REVEAL_MASKED_DATA))
        self.assertFalse(has_permission(Role.ADMIN, Permission.APPROVE_TRANSACTION))

    def test_no_role_holds_every_permission(self) -> None:
        """No single account should be able to run a transaction end to end."""
        everything = set(Permission)
        for role in Role:
            self.assertNotEqual(
                set(describe_matrix()[role.value]),
                {p.value for p in everything},
                f"{role} is omnipotent",
            )

    def test_every_role_has_an_arabic_title(self) -> None:
        for role in Role:
            self.assertTrue(role.arabic_title.strip())


class TestTokens(unittest.TestCase):
    def test_round_trip(self) -> None:
        caller = principal(Role.DIRECTOR, subject="dir-1", department="OPS-DEPT")
        self.assertEqual(caller.subject, "dir-1")
        self.assertEqual(caller.role, Role.DIRECTOR)
        self.assertEqual(caller.department_id, "OPS-DEPT")

    def test_require_raises_for_missing_permission(self) -> None:
        with self.assertRaises(AuthorizationError):
            principal(Role.SPECIALIST).require(Permission.CREATE_PROCUREMENT_ORDER)

    def test_tampered_token_is_rejected(self) -> None:
        token = issue_token("emp-1", Role.SPECIALIST, "IT-DEPT")
        head, payload, signature = token.split(".")
        forged = f"{head}.{payload}.{signature[:-4]}AAAA"
        with self.assertRaises(AuthenticationError):
            verify_token(forged)

    def test_permissions_are_not_read_from_the_token_body(self) -> None:
        """A self-asserted permissions claim must not widen authority.

        This is the attack FR-3.2 has to withstand: a caller who can craft a
        token body cannot grant themselves order:create, because permissions
        are derived from the role at verification time.
        """
        settings = get_settings()
        now = datetime.now(UTC)
        forged = jwt.encode(
            {
                "sub": "attacker",
                "role": Role.SPECIALIST.value,
                "dept": "IT-DEPT",
                "permissions": [Permission.CREATE_PROCUREMENT_ORDER.value],
                "iss": settings.jwt_issuer,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(hours=1)).timestamp()),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        caller = verify_token(forged)
        self.assertFalse(caller.can(Permission.CREATE_PROCUREMENT_ORDER))

    def test_expired_token_is_rejected(self) -> None:
        settings = get_settings()
        past = datetime.now(UTC) - timedelta(hours=2)
        expired = jwt.encode(
            {
                "sub": "emp-1",
                "role": Role.SPECIALIST.value,
                "dept": "IT-DEPT",
                "iss": settings.jwt_issuer,
                "iat": int(past.timestamp()),
                "exp": int((past + timedelta(minutes=1)).timestamp()),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with self.assertRaises(AuthenticationError):
            verify_token(expired)

    def test_unknown_role_is_rejected(self) -> None:
        settings = get_settings()
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "emp-1",
                "role": "superuser",
                "dept": "IT-DEPT",
                "iss": settings.jwt_issuer,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(hours=1)).timestamp()),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with self.assertRaises(AuthenticationError):
            verify_token(token)


if __name__ == "__main__":
    unittest.main()
