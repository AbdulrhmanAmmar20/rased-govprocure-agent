"""Append-only, hash-chained audit trail (FR-5.1).

"غير قابلة للتعديل" - non-modifiable - cannot be enforced by a file permission
alone, because whoever can rotate the log can also rewrite it. What can be
enforced is detection: each record's digest covers the previous record's
digest, so altering or removing any entry breaks every hash after it and
AuditTrail.verify says exactly where.

The file is JSON Lines: one self-describing record per line, appended under a
lock, flushed and fsynced before the call returns. A crash can therefore lose
nothing that was reported as written.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.audit.models import (
    ActorRef,
    ApprovalDecision,
    AuditEvent,
    AuditRecord,
    Citation,
    ToolInvocation,
)
from app.config import get_settings
from app.core.exceptions import AuditIntegrityError
from app.core.logging import get_logger

logger = get_logger("rased.audit")

GENESIS_HASH = "0" * 64


def compute_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over a canonical JSON encoding.

    sort_keys and a fixed separator set make the digest reproducible across
    processes and Python versions, which is what lets a third party re-verify
    the chain from the file alone.
    """
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditTrail:
    """Writer and verifier for one audit file."""

    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = Path(path) if path is not None else settings.audit_log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequence, self._head_hash = self._recover_head()

    def _recover_head(self) -> tuple[int, str]:
        """Resume the chain from an existing file, if there is one."""
        if not self.path.exists():
            return 0, GENESIS_HASH
        last_line = ""
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if not last_line:
            return 0, GENESIS_HASH
        record = json.loads(last_line)
        return int(record["sequence"]), str(record["record_hash"])

    def append(
        self,
        *,
        transaction_id: str,
        event: AuditEvent,
        actor: ActorRef,
        masked_prompt: str = "",
        entity_counts: dict[str, int] | None = None,
        tool_invocations: list[ToolInvocation] | None = None,
        citations: list[Citation] | None = None,
        reasoning_trace: list[str] | None = None,
        approval: ApprovalDecision | None = None,
        outcome: str = "",
        detail: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Write one record and return it, hashes filled in."""
        with self._lock:
            sequence = self._sequence + 1
            draft = AuditRecord(
                record_id=uuid.uuid4().hex,
                sequence=sequence,
                timestamp_utc=datetime.now(UTC),
                transaction_id=transaction_id,
                event=event,
                actor=actor,
                masked_prompt=masked_prompt,
                entity_counts=entity_counts or {},
                tool_invocations=tool_invocations or [],
                citations=citations or [],
                reasoning_trace=reasoning_trace or [],
                approval=approval,
                outcome=outcome,
                detail=detail or {},
                previous_hash=self._head_hash,
            )
            digest = compute_hash(draft.hashable_payload())
            record = draft.model_copy(update={"record_hash": digest})

            line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())

            self._sequence = sequence
            self._head_hash = digest

        logger.info(
            "audit record appended",
            extra={
                "transaction_id": transaction_id,
                "event": event.value,
                "sequence": sequence,
                "actor_role": actor.role,
            },
        )
        return record

    def __iter__(self) -> Iterator[AuditRecord]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield AuditRecord.model_validate_json(line)

    def query(
        self,
        *,
        transaction_id: str | None = None,
        event: AuditEvent | None = None,
        actor_subject: str | None = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        results: list[AuditRecord] = []
        for record in self:
            if transaction_id and record.transaction_id != transaction_id:
                continue
            if event and record.event != event:
                continue
            if actor_subject and record.actor.subject != actor_subject:
                continue
            results.append(record)
        return results[-limit:]

    def verify(self) -> int:
        """Walk the chain end to end. Returns the number of records verified.

        Raises AuditIntegrityError naming the first broken link, so an
        investigator learns not just that the log was touched but where.
        """
        expected_previous = GENESIS_HASH
        expected_sequence = 1
        count = 0

        for record in self:
            if record.sequence != expected_sequence:
                raise AuditIntegrityError(
                    "انقطاع في تسلسل سجل التدقيق.",
                    expected_sequence=expected_sequence,
                    found_sequence=record.sequence,
                    record_id=record.record_id,
                )
            if record.previous_hash != expected_previous:
                raise AuditIntegrityError(
                    "سلسلة التجزئة في سجل التدقيق مكسورة.",
                    sequence=record.sequence,
                    record_id=record.record_id,
                )
            if compute_hash(record.hashable_payload()) != record.record_hash:
                raise AuditIntegrityError(
                    "تم العبث بمحتوى سجل التدقيق.",
                    sequence=record.sequence,
                    record_id=record.record_id,
                )
            expected_previous = record.record_hash
            expected_sequence += 1
            count += 1

        return count


_trail: AuditTrail | None = None


def get_trail() -> AuditTrail:
    global _trail
    if _trail is None:
        _trail = AuditTrail()
    return _trail
