"""FR-3 - the tools and the gateway that guards them."""

from __future__ import annotations

import unittest

from app.config import get_settings
from app.core.rbac import Role
from app.tools.budget import ROUTE_DIRECT, ROUTE_LIMITED, ROUTE_OPEN, verify_budget_ceiling
from app.tools.catalogue import build_gateway
from app.tools.procurement import STATUS_BLOCKED, STATUS_DRAFT, create_procurement_order
from app.tools.vendor import check_vendor_eligibility
from tests.support import SCENARIO_CR, principal


class TestVendorEligibility(unittest.TestCase):
    def test_scenario_vendor_is_eligible(self) -> None:
        """Section 6 must fail on the ceiling, not on an incidental defect."""
        result = check_vendor_eligibility(SCENARIO_CR)
        self.assertTrue(result["eligible"], result["blocking_issues"])

    def test_expired_certificates_block(self) -> None:
        result = check_vendor_eligibility("4030112233")
        self.assertFalse(result["eligible"])
        self.assertTrue(any("الزكاة" in issue for issue in result["blocking_issues"]))

    def test_debarred_vendor_blocks_regardless(self) -> None:
        result = check_vendor_eligibility("2050445566")
        self.assertFalse(result["eligible"])
        self.assertTrue(result["checks"]["debarred"])

    def test_unknown_cr_is_not_eligible(self) -> None:
        result = check_vendor_eligibility("9999999999")
        self.assertFalse(result["found"])
        self.assertFalse(result["eligible"])

    def test_result_carries_its_legal_basis(self) -> None:
        self.assertTrue(check_vendor_eligibility(SCENARIO_CR)["legal_basis"])


class TestBudgetCeiling(unittest.TestCase):
    def setUp(self) -> None:
        self.ceiling = get_settings().direct_purchase_ceiling_sar

    def test_scenario_amount_exceeds_the_ceiling(self) -> None:
        """SRS section 6 step 2 - 120,000 against a 100,000 limit."""
        result = verify_budget_ceiling("IT-DEPT", 120_000)
        self.assertFalse(result["within_ceiling"])
        self.assertEqual(result["ceiling_sar"], self.ceiling)
        self.assertEqual(result["required_route"], ROUTE_LIMITED)

    def test_amount_at_the_ceiling_is_permitted(self) -> None:
        """The limit is inclusive - exactly 100,000 is still direct purchase."""
        result = verify_budget_ceiling("IT-DEPT", self.ceiling)
        self.assertTrue(result["within_ceiling"])
        self.assertEqual(result["required_route"], ROUTE_DIRECT)

    def test_one_riyal_over_is_not(self) -> None:
        result = verify_budget_ceiling("IT-DEPT", self.ceiling + 1)
        self.assertFalse(result["within_ceiling"])

    def test_very_large_amount_requires_open_competition(self) -> None:
        result = verify_budget_ceiling("OPS-DEPT", self.ceiling * 8)
        self.assertEqual(result["required_route"], ROUTE_OPEN)

    def test_insufficient_appropriation_is_reported_separately(self) -> None:
        """A lawful route with no money is still a violation, and a distinct one."""
        result = verify_budget_ceiling("SEC-DEPT", 50_000)
        self.assertTrue(result["within_ceiling"])
        self.assertFalse(result["budget_sufficient"])
        self.assertFalse(result["compliant"])

    def test_near_ceiling_raises_the_splitting_flag(self) -> None:
        result = verify_budget_ceiling("IT-DEPT", self.ceiling * 0.95)
        self.assertTrue(result["near_ceiling"])
        self.assertIn("GTPL-ART-72", result["legal_basis"])

    def test_unknown_department_has_no_appropriation(self) -> None:
        result = verify_budget_ceiling("NOPE-DEPT", 1_000)
        self.assertFalse(result["budget_sufficient"])


class TestOrderCreation(unittest.TestCase):
    def test_over_ceiling_order_is_blocked(self) -> None:
        result = create_procurement_order(SCENARIO_CR, 120_000, department_id="IT-DEPT")
        self.assertEqual(result["status"], STATUS_BLOCKED)
        self.assertTrue(result["blockers"])

    def test_compliant_order_produces_a_draft(self) -> None:
        result = create_procurement_order(SCENARIO_CR, 80_000, department_id="IT-DEPT")
        self.assertEqual(result["status"], STATUS_DRAFT)
        self.assertTrue(result["order_id"])

    def test_draft_is_not_an_award(self) -> None:
        """A draft must say so, so it cannot be mistaken for a decision."""
        result = create_procurement_order(SCENARIO_CR, 80_000, department_id="IT-DEPT")
        self.assertIn("مسودة", result["note"])

    def test_ineligible_vendor_is_blocked(self) -> None:
        result = create_procurement_order("4030112233", 50_000, department_id="IT-DEPT")
        self.assertEqual(result["status"], STATUS_BLOCKED)


class TestGatewayGuardrails(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = build_gateway()
        self.args = {
            "vendor_id": SCENARIO_CR,
            "amount": 80_000.0,
            "department_id": "IT-DEPT",
        }

    def test_specialist_is_denied_order_creation(self) -> None:
        """FR-3.2, the keystone control."""
        result, invocation = self.gateway.invoke(
            "create_procurement_order", self.args, principal=principal(Role.SPECIALIST)
        )
        self.assertTrue(result.denied)
        self.assertFalse(invocation.allowed)
        self.assertIn("order:create", invocation.denial_reason)

    def test_director_is_permitted(self) -> None:
        result, invocation = self.gateway.invoke(
            "create_procurement_order", self.args, principal=principal(Role.DIRECTOR)
        )
        self.assertTrue(invocation.allowed)
        self.assertTrue(result.ok)

    def test_auditor_is_denied_order_creation(self) -> None:
        result, _ = self.gateway.invoke(
            "create_procurement_order", self.args, principal=principal(Role.AUDITOR)
        )
        self.assertTrue(result.denied)

    def test_denial_is_recorded_not_raised(self) -> None:
        """The agent must be able to report the refusal, so it cannot raise."""
        result, invocation = self.gateway.invoke(
            "create_procurement_order", self.args, principal=principal(Role.SPECIALIST)
        )
        self.assertEqual(result.status, "denied")
        self.assertEqual(invocation.tool_name, "create_procurement_order")
        self.assertEqual(invocation.arguments, self.args)

    def test_specialist_may_run_read_only_checks(self) -> None:
        result, _ = self.gateway.invoke(
            "check_vendor_eligibility",
            {"cr_number": SCENARIO_CR},
            principal=principal(Role.SPECIALIST),
        )
        self.assertTrue(result.ok)

    def test_unknown_tool_is_refused(self) -> None:
        result, invocation = self.gateway.invoke(
            "drop_all_records", {}, principal=principal(Role.DIRECTOR)
        )
        self.assertFalse(result.ok)
        self.assertEqual(invocation.outcome, "unknown_tool")

    def test_catalogue_is_filtered_by_permission(self) -> None:
        visible = {s.name for s in self.gateway.available_to(principal(Role.SPECIALIST))}
        self.assertNotIn("create_procurement_order", visible)
        self.assertIn("check_vendor_eligibility", visible)

    def test_bad_arguments_do_not_crash_the_gateway(self) -> None:
        result, invocation = self.gateway.invoke(
            "verify_budget_ceiling", {"wrong": 1}, principal=principal(Role.SPECIALIST)
        )
        self.assertFalse(result.ok)
        self.assertEqual(invocation.outcome, "bad_arguments")


if __name__ == "__main__":
    unittest.main()
