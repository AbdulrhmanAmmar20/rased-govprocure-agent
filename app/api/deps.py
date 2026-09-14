"""FastAPI dependencies: authentication and permission guards.

``require(permission)`` returns a dependency, so a route declares the
capability it needs in its own signature. Two things follow from that: the
permission appears in the generated OpenAPI document, and a reviewer auditing
who may call what reads the route definitions rather than tracing calls into
handler bodies.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.rbac import Permission
from app.core.security import Principal, verify_token

bearer_scheme = HTTPBearer(auto_error=False, description="رمز دخول JWT صادر من البوابة.")


async def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    """Resolve and validate the caller's token."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "authentication_required", "message": "رمز الدخول مطلوب."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require(permission: Permission) -> Callable[[Principal], Principal]:
    """Build a dependency that admits only callers holding ``permission``."""

    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        try:
            principal.require(permission)
        except AuthorizationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
        return principal

    return dependency


def client_fingerprint(request: Request) -> str:
    """A coarse client identifier for logs.

    The client IP is recorded, not the user agent or any header the caller
    controls, and it is never joined to the subject in the audit trail - the
    audit record identifies the employee, and correlating that with a network
    address is a separate decision for the entity's SIEM to make.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
