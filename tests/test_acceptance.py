"""SRS section 6 - the user acceptance scenario, step by step.

This is the test the specification itself asks for. Each method corresponds to
one numbered step of the scenario, so a failure names the requirement that
broke rather than the function that raised.
"""

from __future__ import annotations

import unittest

from app.agent.service import decide_approval, review_transaction
from app.agent.state import TransactionStatus
from app.core.exceptions import AuthorizationError
from app.core.rbac import Role
from app.tools.budget import BASIS_CEILING
from tests.support import SCENARIO_CR, SCENARIO_REQUEST, principal, temp_trail


class TestAcceptanceScenario(unittest.TestCase):
    """The whole scenario, run once and asserted step by step."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trail = temp_trail()
        cls.specialist = principal(Role.SPECIALIST, subject="emp-1042")
        cls.state = review_transaction(
            SCENARIO_REQUEST, cls.specialist, transaction_id="TX-UAT-001", trail=cls.trail
        )

    # -- Step 1: interception and masking ---------------------------------

    def test_step1_cr_number_is_masked(self) -> None:
        """The CR is replaced by <CR_NUM_1>."""
        self.assertIn("<CR_NUM_1>", self.state.masked_request)

    def test_step1_original_cr_does_not_survive_in_the_masked_text(self) -> None:
        self.assertNotIn(SCENARIO_CR, self.state.masked_request)

    def test_step1_masking_is_recorded_in_the_audit_trail(self) -> None:
        self.assertIn("pii.masked", [r.event.value for r in self.trail])

    def test_step1_audit_trail_never_stores_the_raw_request(self) -> None:
        """FR-5.1 records the masked prompt; the original must not appear."""
        for record in self.trail:
            self.assertNotIn(SCENARIO_CR, record.masked_prompt)
            self.assertNotIn(SCENARIO_CR, str(record.detail))

    # -- Step 2: regulatory audit -----------------------------------------

    def test_step2_direct_purchase_article_is_retrieved(self) -> None:
        """The agent retrieves the article governing direct purchase."""
        articles = {c.article for c in self.state.citations}
        self.assertTrue(articles)
        self.assertTrue(
            any("الثلاثون" in article for article in articles),
            f"expected the ceiling article among {articles}",
        )

    def test_step2_ceiling_violation_is_detected(self) -> None:
        """120,000 against a statutory ceiling of 100,000."""
        codes = {f.code for f in self.state.findings}
        self.assertIn("DIRECT_PURCHASE_CEILING_EXCEEDED", codes)

    def test_step2_finding_carries_its_legal_basis(self) -> None:
        """FR-2.2 - no finding without an article behind it."""
        violation = next(
            f for f in self.state.findings if f.code == "DIRECT_PURCHASE_CEILING_EXCEEDED"
        )
        self.assertIn(BASIS_CEILING, violation.legal_basis)

    def test_step2_alternative_route_is_identified(self) -> None:
        """The report must say what to do, not only that it is forbidden."""
        budget = self.state.tool_results["verify_budget_ceiling"]
        self.assertEqual(budget["required_route"], "limited_competition")

    # -- Step 3: authority check ------------------------------------------

    def test_step3_agent_actually_attempted_the_order(self) -> None:
        """The refusal must be a recorded event, not a hypothetical."""
        attempted = {t.tool_name for t in self.state.tool_invocations}
        self.assertIn("create_procurement_order", attempted)

    def test_step3_rbac_denied_the_specialist(self) -> None:
        """The gateway blocks the call because the caller is not a Director."""
        attempt = next(
            t for t in self.state.tool_invocations if t.tool_name == "create_procurement_order"
        )
        self.assertFalse(attempt.allowed)
        self.assertIn("order:create", attempt.denial_reason)

    def test_step3_denial_is_in_the_audit_trail(self) -> None:
        self.assertIn("tool.denied", [r.event.value for r in self.trail])

    # -- Step 4: the final output -----------------------------------------

    def test_step4_report_is_issued(self) -> None:
        self.assertTrue(self.state.report_ar.strip())

    def test_step4_report_names_the_ceiling_violation(self) -> None:
        self.assertIn("السقف", self.state.report_ar)

    def test_step4_report_requires_the_approver(self) -> None:
        """The transaction requires the authorised approver's decision."""
        self.assertIn("صاحب الصلاحية", self.state.report_ar)

    def test_step4_transaction_is_suspended(self) -> None:
        """FR-4.1 - status moves to PENDING_APPROVAL."""
        self.assertIs(self.state.status, TransactionStatus.PENDING_APPROVAL)
        self.assertTrue(self.state.requires_approval)

    def test_step4_reasoning_trace_is_available(self) -> None:
        """NFR-3.1 - a reviewer can see why the agent concluded what it did."""
        self.assertTrue(self.state.reasoning_trace)

    def test_step4_incident_is_in_the_audit_trail(self) -> None:
        """The incident is documented in the audit log."""
        events = [r.event.value for r in self.trail]
        for expected in (
            "transaction.submitted",
            "pii.masked",
            "regulation.retrieved",
            "violation.detected",
            "tool.denied",
            "report.issued",
            "approval.requested",
        ):
            self.assertIn(expected, events)

    def test_step4_audit_chain_verifies(self) -> None:
        self.assertGreater(self.trail.verify(), 0)


class TestApprovalRoute(unittest.TestCase):
    """FR-4.2 - what a Director can do with the suspended transaction."""

    def setUp(self) -> None:
        self.trail = temp_trail()
        self.state = review_transaction(
            SCENARIO_REQUEST,
            principal(Role.SPECIALIST, subject="emp-1042"),
            trail=self.trail,
        )

    def test_specialist_cannot_approve_their_own_transaction(self) -> None:
        with self.assertRaises(AuthorizationError):
            decide_approval(
                self.state.transaction_id,
                principal(Role.SPECIALIST, subject="emp-1042"),
                approved=True,
                trail=self.trail,
            )

    def test_director_can_approve_with_notes(self) -> None:
        resolved = decide_approval(
            self.state.transaction_id,
            principal(Role.DIRECTOR, subject="dir-7"),
            approved=True,
            notes="موافقة استثنائية موثقة.",
            trail=self.trail,
        )
        self.assertIs(resolved.status, TransactionStatus.APPROVED)
        self.assertIn("approval.granted", [r.event.value for r in self.trail])

    def test_director_can_reject(self) -> None:
        resolved = decide_approval(
            self.state.transaction_id,
            principal(Role.DIRECTOR, subject="dir-7"),
            approved=False,
            notes="يُحوّل لمنافسة محدودة.",
            trail=self.trail,
        )
        self.assertIs(resolved.status, TransactionStatus.REJECTED)

    def test_decision_records_the_approver_identity(self) -> None:
        decide_approval(
            self.state.transaction_id,
            principal(Role.DIRECTOR, subject="dir-7"),
            approved=True,
            notes="تم.",
            trail=self.trail,
        )
        record = next(r for r in self.trail if r.event.value == "approval.granted")
        self.assertEqual(record.approval.approver_subject, "dir-7")
        self.assertEqual(record.approval.approver_role, "director")


if __name__ == "__main__":
    unittest.main()
