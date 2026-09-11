"""Tool registry and execution gateway (FR-3.1, FR-3.2).

FR-3.2 requires the RBAC check to live in the code layer. That phrasing is
doing real work: an agent that is merely *told* in its prompt not to call a
tool will eventually call it, because the prompt is a request and the caller's
input can argue with it. Here the permission is bound to the tool declaration
itself, and invoke() checks it before the handler is reachable. There is no
path to a handler that skips the check.

A denial returns a ToolResult rather than raising. The acceptance scenario
depends on that: the agent must attempt the order, be refused, and then write
the refusal into its report. An exception would abort the run and lose the
finding the whole exercise exists to produce.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.audit.models import ToolInvocation
from app.core.exceptions import AuthorizationError, ToolExecutionError
from app.core.logging import get_logger
from app.core.rbac import Permission
from app.core.security import Principal

logger = get_logger("rased.tools")


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Outcome of one attempted invocation."""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    denied: bool = False

    @property
    def status(self) -> str:
        if self.denied:
            return "denied"
        return "ok" if self.ok else "error"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool the agent may attempt, and the permission it costs."""

    name: str
    description: str
    required_permission: Permission
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    # Tools that change state outside Rased. These are the ones a Director
    # must personally stand behind, and the graph routes them to approval.
    mutating: bool = False

    def to_schema(self) -> dict[str, Any]:
        """OpenAI-style function schema, for models that support tool calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolGateway:
    """Holds the tool catalogue and is the only way to execute one."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def available_to(self, principal: Principal) -> list[ToolSpec]:
        """The subset this caller could actually execute.

        Used to build the prompt, so the agent is not shown tools it will be
        refused — being refused is still handled correctly, but there is no
        reason to invite the attempt.
        """
        return [
            spec
            for spec in self._tools.values()
            if principal.can(spec.required_permission)
        ]

    def schemas_for(self, principal: Principal) -> list[dict[str, Any]]:
        return [spec.to_schema() for spec in self.available_to(principal)]

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        principal: Principal,
    ) -> tuple[ToolResult, ToolInvocation]:
        """Execute a tool after checking authority. Returns result and audit entry."""
        started = time.perf_counter()
        spec = self._tools.get(name)

        if spec is None:
            result = ToolResult(ok=False, error=f"أداة غير معروفة: {name}")
            return result, ToolInvocation(
                tool_name=name,
                arguments=arguments,
                allowed=False,
                outcome="unknown_tool",
                denial_reason="tool not registered",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        # FR-3.2 — the gate. Nothing below runs without the permission.
        try:
            principal.require(spec.required_permission)
        except AuthorizationError as exc:
            logger.warning(
                "tool invocation denied",
                extra={
                    "tool": name,
                    "subject": principal.subject,
                    "role": principal.role.value,
                    "required_permission": spec.required_permission.value,
                },
            )
            result = ToolResult(ok=False, denied=True, error=exc.message)
            return result, ToolInvocation(
                tool_name=name,
                arguments=arguments,
                allowed=False,
                outcome="denied",
                denial_reason=(
                    f"role '{principal.role.value}' lacks "
                    f"'{spec.required_permission.value}'"
                ),
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            data = spec.handler(**arguments)
            result = ToolResult(ok=True, data=data)
            outcome = "ok"
            error = ""
        except TypeError as exc:
            result = ToolResult(ok=False, error=f"معاملات غير صحيحة: {exc}")
            outcome, error = "bad_arguments", str(exc)
        except ToolExecutionError as exc:
            result = ToolResult(ok=False, error=exc.message)
            outcome, error = "failed", exc.message
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool raised", extra={"tool": name})
            result = ToolResult(ok=False, error="فشل تنفيذ الأداة.")
            outcome, error = "failed", str(exc)

        return result, ToolInvocation(
            tool_name=name,
            arguments=arguments,
            allowed=True,
            outcome=outcome,
            denial_reason=error if outcome != "ok" else "",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
