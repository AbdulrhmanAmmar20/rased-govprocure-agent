"""JWT issuing and verification (FR-3.2).

Tokens are the only thing the tool gateway trusts. A token carries the caller's
role, and the permission set is *derived* from that role at verification time
rather than read from the token body — a caller cannot widen their own
authority by editing an unsigned claim, and a change to the RBAC matrix takes
effect immediately instead of when outstanding tokens expire.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.rbac import Permission, Role, permissions_for


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as the rest of the system sees them."""

    subject: str
    role: Role
    department_id: str
    display_name: str = ""
    token_id: str = ""
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.role)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        """Raise unless the caller holds ``permission``.

        This is the single chokepoint every guarded operation goes through.
        """
        if not self.can(permission):
            raise AuthorizationError(
                f"الدور '{self.role.arabic_title}' لا يملك صلاحية '{permission.value}'.",
                subject=self.subject,
                role=self.role.value,
                required_permission=permission.value,
            )

    def audit_identity(self) -> dict[str, str]:
        """The identity block embedded in every audit record (FR-5.1)."""
        return {
            "subject": self.subject,
            "role": self.role.value,
            "department_id": self.department_id,
            "token_id": self.token_id,
        }


def issue_token(
    subject: str,
    role: Role,
    department_id: str,
    *,
    display_name: str = "",
    settings: Settings | None = None,
) -> str:
    """Mint a signed access token for a known identity.

    In production the identity assertion arrives from the entity's IAM / Nafath
    federation; this function is the last mile that turns it into a Rased token.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": Role(role).value,
        "dept": department_id,
        "name": display_name,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_ttl_seconds)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_token(token: str, *, settings: Settings | None = None) -> Principal:
    """Validate a token and project it onto a :class:`Principal`."""
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("انتهت صلاحية رمز الدخول.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(f"رمز دخول غير صالح: {exc}") from exc

    try:
        role = Role(claims["role"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("رمز الدخول لا يحمل دوراً معروفاً.") from exc

    return Principal(
        subject=str(claims["sub"]),
        role=role,
        department_id=str(claims.get("dept", "")),
        display_name=str(claims.get("name", "")),
        token_id=str(claims.get("jti", "")),
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
    )
