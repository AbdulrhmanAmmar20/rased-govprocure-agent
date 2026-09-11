"""FR-5 - the audit trail and its tamper-evidence."""

from __future__ import annotations

import json
import unittest

from app.audit.models import ActorRef, AuditEvent, ToolInvocation
from app.audit.trail import GENESIS_HASH, AuditTrail, compute_hash
from app.core.exceptions import AuditIntegrityError
from tests.support import SCENARIO_CR, temp_trail

ACTOR = ActorRef(subject="emp-1042", role="specialist", department_id="IT-DEPT", token_id="t1")


class TestChain(unittest.TestCase):
    def setUp(self) -> None:
        self.trail = temp_trail()

    def _append(self, n: int = 3) -> None:
        for index in range(n):
            self.trail.append(
                transaction_id="tx-1",
                event=AuditEvent.TRANSACTION_SUBMITTED,
                actor=ACTOR,
                outcome=f"step-{index}",
            )

    def test_first_record_links_to_genesis(self) -> None:
        record = self.trail.append(
            transaction_id="tx-1", event=AuditEvent.PII_MASKED, actor=ACTOR
        )
        self.assertEqual(record.previous_hash, GENESIS_HASH)
        self.assertEqual(record.sequence, 1)

    def test_sequence_increments(self) -> None:
        self._append(3)
        self.assertEqual([r.sequence for r in self.trail], [1, 2, 3])

    def test_each_record_links_to_its_predecessor(self) -> None:
        self._append(3)
        records = list(self.trail)
        for previous, current in zip(records[:-1], records[1:], strict=True):
            self.assertEqual(current.previous_hash, previous.record_hash)

    def test_clean_chain_verifies(self) -> None:
        self._append(4)
        self.assertEqual(self.trail.verify(), 4)

    def test_hash_is_reproducible(self) -> None:
        """A third party must be able to recompute the chain from the file."""
        record = self.trail.append(
            transaction_id="tx-1", event=AuditEvent.REPORT_ISSUED, actor=ACTOR
        )
        self.assertEqual(compute_hash(record.hashable_payload()), record.record_hash)

    def test_a_new_writer_resumes_the_existing_chain(self) -> None:
        self._append(2)
        resumed = AuditTrail(self.trail.path)
        resumed.append(transaction_id="tx-1", event=AuditEvent.REPORT_ISSUED, actor=ACTOR)
        self.assertEqual(resumed.verify(), 3)


class TestTamperDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.trail = temp_trail()
        for index in range(3):
            self.trail.append(
                transaction_id="tx-1",
                event=AuditEvent.TOOL_INVOKED,
                actor=ACTOR,
                outcome=f"step-{index}",
            )

    def _rewrite(self, lines: list[str]) -> None:
        self.trail.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_edited_content_is_detected(self) -> None:
        """Slipping an unmasked identifier into the log must not go unnoticed."""
        lines = self.trail.path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[1])
        record["masked_prompt"] = f"... {SCENARIO_CR} ..."
        lines[1] = json.dumps(record, ensure_ascii=False)
        self._rewrite(lines)

        with self.assertRaises(AuditIntegrityError) as caught:
            self.trail.verify()
        self.assertEqual(caught.exception.details["sequence"], 2)

    def test_deleted_record_is_detected(self) -> None:
        lines = self.trail.path.read_text(encoding="utf-8").splitlines()
        del lines[1]
        self._rewrite(lines)
        with self.assertRaises(AuditIntegrityError):
            self.trail.verify()

    def test_reordered_records_are_detected(self) -> None:
        lines = self.trail.path.read_text(encoding="utf-8").splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        self._rewrite(lines)
        with self.assertRaises(AuditIntegrityError):
            self.trail.verify()

    def test_recomputed_hash_still_breaks_the_chain(self) -> None:
        """An attacker who fixes one record's own hash still breaks the links.

        This is the property that makes the chain worth having: correcting a
        single digest is not enough, because every later record commits to it.
        """
        lines = self.trail.path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["outcome"] = "tampered"
        record["record_hash"] = compute_hash(
            {k: v for k, v in record.items() if k != "record_hash"}
        )
        lines[0] = json.dumps(record, ensure_ascii=False)
        self._rewrite(lines)

        with self.assertRaises(AuditIntegrityError):
            self.trail.verify()


class TestRecordContent(unittest.TestCase):
    def test_denied_tool_attempts_are_recorded(self) -> None:
        """A refusal is the evidence FR-3.2 held; it must be kept."""
        trail = temp_trail()
        trail.append(
            transaction_id="tx-1",
            event=AuditEvent.TOOL_DENIED,
            actor=ACTOR,
            tool_invocations=[
                ToolInvocation(
                    tool_name="create_procurement_order",
                    arguments={"amount": 120000.0},
                    allowed=False,
                    denial_reason="role 'specialist' lacks 'order:create'",
                )
            ],
        )
        record = next(iter(trail))
        self.assertFalse(record.tool_invocations[0].allowed)
        self.assertEqual(record.tool_invocations[0].arguments["amount"], 120000.0)

    def test_records_identify_actor_and_role(self) -> None:
        trail = temp_trail()
        trail.append(transaction_id="tx-1", event=AuditEvent.PII_MASKED, actor=ACTOR)
        record = next(iter(trail))
        self.assertEqual(record.actor.subject, "emp-1042")
        self.assertEqual(record.actor.role, "specialist")
        self.assertIsNotNone(record.timestamp_utc.tzinfo)

    def test_query_filters_by_transaction(self) -> None:
        trail = temp_trail()
        trail.append(transaction_id="tx-1", event=AuditEvent.PII_MASKED, actor=ACTOR)
        trail.append(transaction_id="tx-2", event=AuditEvent.PII_MASKED, actor=ACTOR)
        self.assertEqual(len(trail.query(transaction_id="tx-1")), 1)


if __name__ == "__main__":
    unittest.main()
