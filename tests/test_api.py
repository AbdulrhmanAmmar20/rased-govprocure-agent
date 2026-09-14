"""HTTP surface: authentication, authorisation and response hygiene."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import create_app
from tests.support import SCENARIO_CR, SCENARIO_REQUEST

PREFIX = "/api/v1"


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def token(self, role: str, subject: str = "emp-1", department: str = "IT-DEPT") -> dict:
        response = self.client.post(
            f"{PREFIX}/auth/token",
            json={"subject": subject, "role": role, "department_id": department},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestHealth(ApiTestCase):
    def test_liveness_needs_no_token(self) -> None:
        response = self.client.get(f"{PREFIX}/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_readiness_reports_region_and_corpus_status(self) -> None:
        body = self.client.get(f"{PREFIX}/health/ready").json()
        self.assertTrue(body["region"].startswith("sa-"))
        self.assertIn("corpus_status", body)

    def test_security_headers_are_set(self) -> None:
        headers = self.client.get(f"{PREFIX}/health/live").headers
        self.assertIn("Strict-Transport-Security", headers)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")


class TestAuthentication(ApiTestCase):
    def test_review_requires_a_token(self) -> None:
        response = self.client.post(f"{PREFIX}/procurement/review", json={"text": SCENARIO_REQUEST})
        self.assertEqual(response.status_code, 401)

    def test_garbage_token_is_rejected(self) -> None:
        response = self.client.post(
            f"{PREFIX}/procurement/review",
            json={"text": SCENARIO_REQUEST},
            headers={"Authorization": "Bearer not-a-token"},
        )
        self.assertEqual(response.status_code, 401)

    def test_me_reports_derived_permissions(self) -> None:
        body = self.client.get(f"{PREFIX}/auth/me", headers=self.token("director")).json()
        self.assertIn("order:create", body["permissions"])
        self.assertEqual(body["role"], "director")


class TestReviewEndpoint(ApiTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        headers = cls().token("specialist", subject="emp-1042")
        cls.headers = headers
        cls.response = cls.client.post(
            f"{PREFIX}/procurement/review", json={"text": SCENARIO_REQUEST}, headers=headers
        )
        cls.body = cls.response.json()

    def test_review_succeeds(self) -> None:
        self.assertEqual(self.response.status_code, 200, self.response.text)

    def test_response_is_masked(self) -> None:
        self.assertIn("<CR_NUM_1>", self.body["masked_request"])

    def test_response_never_leaks_the_original_identifier(self) -> None:
        """The whole payload, not just the masked field, must be clean."""
        self.assertNotIn(SCENARIO_CR, self.response.text)

    def test_response_carries_findings_and_citations(self) -> None:
        self.assertTrue(self.body["findings"])
        self.assertTrue(self.body["citations"])

    def test_transaction_is_suspended(self) -> None:
        self.assertEqual(self.body["status"], "PENDING_APPROVAL")

    def test_short_input_is_rejected_by_validation(self) -> None:
        response = self.client.post(
            f"{PREFIX}/procurement/review", json={"text": "قصير"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)


class TestDisclosureEndpoint(ApiTestCase):
    def test_authorised_caller_can_reveal(self) -> None:
        headers = self.token("specialist", subject="emp-1042")
        review = self.client.post(
            f"{PREFIX}/procurement/review", json={"text": SCENARIO_REQUEST}, headers=headers
        ).json()
        response = self.client.post(
            f"{PREFIX}/procurement/reveal",
            json={"transaction_id": review["transaction_id"], "text": review["masked_request"]},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(SCENARIO_CR, response.json()["revealed_text"])

    def test_admin_is_refused(self) -> None:
        spec = self.token("specialist", subject="emp-1042")
        review = self.client.post(
            f"{PREFIX}/procurement/review", json={"text": SCENARIO_REQUEST}, headers=spec
        ).json()
        response = self.client.post(
            f"{PREFIX}/procurement/reveal",
            json={"transaction_id": review["transaction_id"], "text": review["masked_request"]},
            headers=self.token("admin", subject="adm-1", department="SEC-DEPT"),
        )
        self.assertEqual(response.status_code, 403)


class TestApprovalEndpoints(ApiTestCase):
    def test_specialist_cannot_see_the_approval_queue(self) -> None:
        response = self.client.get(f"{PREFIX}/approvals/pending", headers=self.token("specialist"))
        self.assertEqual(response.status_code, 403)

    def test_director_sees_the_queue_and_can_decide(self) -> None:
        spec = self.token("specialist", subject="emp-1042")
        review = self.client.post(
            f"{PREFIX}/procurement/review", json={"text": SCENARIO_REQUEST}, headers=spec
        ).json()
        director = self.token("director", subject="dir-7")

        queue = self.client.get(f"{PREFIX}/approvals/pending", headers=director)
        self.assertEqual(queue.status_code, 200)

        decision = self.client.post(
            f"{PREFIX}/approvals/{review['transaction_id']}/decide",
            json={"approved": False, "notes": "يُحوّل لمنافسة محدودة."},
            headers=director,
        )
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["status"], "REJECTED")

    def test_unknown_transaction_is_404(self) -> None:
        response = self.client.post(
            f"{PREFIX}/approvals/TX-NOPE/decide",
            json={"approved": True},
            headers=self.token("director", subject="dir-7"),
        )
        self.assertEqual(response.status_code, 404)


class TestAuditEndpoints(ApiTestCase):
    def test_specialist_cannot_read_the_audit_log(self) -> None:
        response = self.client.get(f"{PREFIX}/audit/records", headers=self.token("specialist"))
        self.assertEqual(response.status_code, 403)

    def test_auditor_can_read_and_verify(self) -> None:
        headers = self.token("auditor", subject="aud-3", department="AUDIT")
        listing = self.client.get(f"{PREFIX}/audit/records", headers=headers)
        self.assertEqual(listing.status_code, 200)
        body = self.client.get(f"{PREFIX}/audit/verify", headers=headers).json()
        self.assertTrue(body["verified"], body["message"])

    def test_record_listing_does_not_expose_prompts(self) -> None:
        """A list view must not make browsing everyone's submissions trivial."""
        headers = self.token("auditor", subject="aud-3", department="AUDIT")
        records = self.client.get(f"{PREFIX}/audit/records", headers=headers).json()
        for record in records:
            self.assertNotIn("masked_prompt", record)


if __name__ == "__main__":
    unittest.main()
