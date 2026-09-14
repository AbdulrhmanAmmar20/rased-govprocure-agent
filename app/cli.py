"""Command-line entry point (``rased``).

Exists so the compliance path can be walked without an HTTP client, which
matters in two situations that come up repeatedly: demonstrating the SRS
scenario to a reviewer who will not install anything, and verifying the audit
chain on a host where the API is down.
"""

from __future__ import annotations

import argparse
import json
import sys

from app import __version__
from app.audit.trail import get_trail
from app.core.exceptions import AuditIntegrityError, RasedError
from app.core.logging import configure_logging
from app.core.rbac import Role, describe_matrix
from app.core.security import issue_token, verify_token

SCENARIO_REQUEST = (
    "أرغب في ترسية شراء مباشر لكاميرات مراقبة على مؤسسة الأفق "
    "سجل تجاري 1010998877 بمبلغ 120,000 ريال وفق نظام المنافسات."
)

_RULE = "=" * 72


def _print(text: str = "") -> None:
    """Write to stdout, tolerating a console that cannot encode Arabic.

    Windows consoles default to a legacy code page; without this the demo dies
    on a UnicodeEncodeError rather than showing the report it exists to show.
    """
    stream = sys.stdout
    encoding = stream.encoding or "utf-8"
    stream.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the SRS section 6 acceptance scenario and print the result."""
    from app.agent.service import decide_approval, review_transaction

    principal = verify_token(
        issue_token("emp-1042", Role(args.role), args.department, display_name="موظف تجريبي")
    )
    request_text = args.text or SCENARIO_REQUEST

    _print(_RULE)
    _print(f"المدخل ({principal.role.arabic_title}):")
    _print(f"  {request_text}")
    _print(_RULE)

    state = review_transaction(request_text, principal)

    _print("\n[1] بعد الحجب (FR-1):")
    _print(f"  {state.masked_request}")

    _print("\n[2] السند النظامي المسترجع (FR-2):")
    for citation in state.citations[:3]:
        _print(f"  - {citation.document} — {citation.article}  ({citation.score:.3f})")

    _print("\n[3] محاولات استدعاء الأدوات (FR-3):")
    for invocation in state.tool_invocations:
        verdict = "مسموح" if invocation.allowed else "ممنوع"
        _print(f"  - {invocation.tool_name}: {verdict} ({invocation.outcome})")
        if invocation.denial_reason:
            _print(f"      السبب: {invocation.denial_reason}")

    _print("\n[4] التقرير النظامي:")
    for line in state.report_ar.splitlines():
        _print(f"  {line}")

    _print(f"\n[5] حالة المعاملة: {state.status.value}")

    if args.approve and state.requires_approval:
        director = verify_token(issue_token("dir-7", Role.DIRECTOR, args.department))
        resolved = decide_approval(
            state.transaction_id, director, approved=True, notes="موافقة استثنائية موثقة."
        )
        _print(f"      بعد اعتماد صاحب الصلاحية: {resolved.status.value}")

    _print("\n[6] مسار تفكير الوكيل (NFR-3.1):")
    for step in state.reasoning_trace:
        _print(f"  {step}")

    _print(f"\nمعرّف المعاملة: {state.transaction_id}")
    _print(_RULE)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify the audit chain (FR-5.1)."""
    try:
        count = get_trail().verify()
    except AuditIntegrityError as exc:
        _print(f"سلسلة سجل التدقيق مكسورة: {exc.message}")
        _print(f"التفاصيل: {json.dumps(exc.details, ensure_ascii=False)}")
        return 1
    _print(f"سلسلة سجل التدقيق سليمة. عدد السجلات المتحقق منها: {count}")
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    """Print the RBAC matrix."""
    for role, permissions in describe_matrix().items():
        _print(f"\n{role} ({Role(role).arabic_title}):")
        for permission in permissions:
            _print(f"  - {permission}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Shared options are attached to every subparser as well as the root, so
    # both "rased --quiet demo" and the more natural "rased demo --quiet" work.
    # argparse otherwise accepts only the former, which reads as a bug.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--quiet", action="store_true", help="اكتم السجلات التشغيلية")

    parser = argparse.ArgumentParser(
        prog="rased",
        description="راصد — وكيل الامتثال لتدقيق المشتريات الحكومية",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"rased {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser(
        "demo", help="تشغيل سيناريو القبول من القسم السادس", parents=[common]
    )
    demo.add_argument("--text", default="", help="نص طلب بديل")
    demo.add_argument("--role", default=Role.SPECIALIST.value, choices=[r.value for r in Role])
    demo.add_argument("--department", default="IT-DEPT")
    demo.add_argument("--approve", action="store_true", help="اعتماد المعاملة بعد الفحص")
    demo.set_defaults(func=cmd_demo)

    verify = sub.add_parser(
        "verify-audit", help="التحقق من سلامة سجل التدقيق", parents=[common]
    )
    verify.set_defaults(func=cmd_verify)

    matrix = sub.add_parser("rbac", help="عرض مصفوفة الصلاحيات", parents=[common])
    matrix.set_defaults(func=cmd_matrix)

    args = parser.parse_args(argv)
    configure_logging("CRITICAL" if args.quiet else "WARNING")

    try:
        return int(args.func(args))
    except RasedError as exc:
        _print(f"خطأ [{exc.code}]: {exc.message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
