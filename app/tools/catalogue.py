"""The registered tool catalogue (FR-3.1).

Kept apart from the gateway so that the gateway has no knowledge of any
particular tool, and apart from the tool modules so that importing one does
not implicitly register it. Tests build their own gateway from the same
specs with substituted backends.
"""

from __future__ import annotations

from app.tools import budget, procurement, vendor
from app.tools.registry import ToolGateway, ToolSpec

ALL_SPECS: tuple[ToolSpec, ...] = (
    vendor.SPEC,
    budget.SPEC,
    procurement.SPEC,
)


def build_gateway(specs: tuple[ToolSpec, ...] = ALL_SPECS) -> ToolGateway:
    gateway = ToolGateway()
    for spec in specs:
        gateway.register(spec)
    return gateway


_gateway: ToolGateway | None = None


def get_gateway() -> ToolGateway:
    global _gateway
    if _gateway is None:
        _gateway = build_gateway()
    return _gateway
