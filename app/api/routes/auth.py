"""Token issuance and identity introspection.

The token endpoint is a development affordance. It exists so the SRS scenario
can be walked end to end without standing up an identity provider, and
app.main refuses to mount it when the environment is not development — an
endpoint that mints a Director token for anyone who asks is the single most
dangerous thing in this repository if it ever reaches production.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import current_principal, require
from app.api.schemas import TokenRequest, TokenResponse
from app.config import get_settings
from app.core.rbac import Permission, describe_matrix
from app.core.security import Principal, issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse, summary="إصدار رمز دخول (بيئة التطوير فقط)")
async def create_token(payload: TokenRequest) -> TokenResponse:
    settings = get_settings()
    token = issue_token(
        payload.subject,
        payload.role,
        payload.department_id,
        display_name=payload.display_name,
    )
    from app.core.rbac import permissions_for

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_ttl_seconds,
        role=payload.role.value,
        permissions=sorted(p.value for p in permissions_for(payload.role)),
    )


@router.get("/me", summary="بيانات المستخدم الحالي وصلاحياته")
async def me(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "subject": principal.subject,
        "role": principal.role.value,
        "role_ar": principal.role.arabic_title,
        "department_id": principal.department_id,
        "permissions": sorted(p.value for p in principal.permissions),
    }


@router.get(
    "/rbac-matrix",
    summary="مصفوفة الأدوار والصلاحيات",
    dependencies=[Depends(require(Permission.VERIFY_AGENT_CITATIONS))],
)
async def rbac_matrix() -> dict:
    """The full matrix, for a Compliance Auditor to check against policy.

    Guarded rather than public: the matrix tells a reader exactly which role to
    target to reach create_procurement_order.
    """
    return {"roles": describe_matrix()}
