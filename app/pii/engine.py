"""The masking engine (FR-1.1, FR-1.2, FR-1.3).

This is the gate. Text arriving at the API is masked here before it reaches
retrieval, the prompt, or the inference endpoint, and the resulting mapping
lives only in the encrypted session vault.

Placeholder allocation is deterministic within a session: the same value
always yields the same placeholder. Without that property a vendor mentioned
twice in one submission would appear to the model as two different vendors,
and the agent's reasoning about them would be wrong.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import Counter
from dataclasses import dataclass, field

from app.config import get_settings
from app.core.logging import get_logger
from app.pii.entities import DetectedEntity, EntityType, Recognizer, resolve_conflicts
from app.pii.recognizers import default_recognizers
from app.pii.vault import SessionVault, registry

logger = get_logger("rased.pii")


@dataclass(frozen=True, slots=True)
class MaskingResult:
    """What the gate produces. masked_text is the only field safe to send out."""

    masked_text: str
    original_length: int
    entities: tuple[DetectedEntity, ...]
    placeholders: dict[str, str]
    duration_ms: float
    session_id: str
    within_budget: bool

    @property
    def entity_counts(self) -> dict[str, int]:
        return dict(Counter(e.entity_type.value for e in self.entities))

    def audit_summary(self) -> dict[str, object]:
        """Safe to persist: counts and placeholder names, never source values."""
        return {
            "masked_prompt": self.masked_text,
            "entity_counts": self.entity_counts,
            "placeholders": sorted(self.placeholders),
            "masking_duration_ms": round(self.duration_ms, 2),
            "within_latency_budget": self.within_budget,
        }


@dataclass
class _Allocator:
    """Assigns placeholder names for one session.

    Values are indexed by HMAC rather than by the value itself, so the
    allocator - which is not encrypted - never holds plaintext PII.
    """

    key: bytes
    counters: Counter = field(default_factory=Counter)
    by_fingerprint: dict[str, str] = field(default_factory=dict)

    def _fingerprint(self, entity_type: EntityType, value: str) -> str:
        message = f"{entity_type.value}:{value}".encode()
        return hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def allocate(self, entity_type: EntityType, value: str) -> tuple[str, bool]:
        """Return (placeholder, is_new) for this value."""
        fingerprint = self._fingerprint(entity_type, value)
        existing = self.by_fingerprint.get(fingerprint)
        if existing is not None:
            return existing, False

        self.counters[entity_type] += 1
        placeholder = f"<{entity_type.placeholder_tag}_{self.counters[entity_type]}>"
        self.by_fingerprint[fingerprint] = placeholder
        return placeholder, True


class MaskingEngine:
    """Detects sensitive entities and swaps them for placeholders."""

    def __init__(
        self,
        recognizers: list[Recognizer] | None = None,
        *,
        min_score: float = 0.60,
    ) -> None:
        self.recognizers = recognizers if recognizers is not None else default_recognizers()
        self.min_score = min_score
        self._allocators: dict[str, _Allocator] = {}

    def detect(self, text: str) -> list[DetectedEntity]:
        """Run every recognizer over the full text and settle overlaps."""
        hits: list[DetectedEntity] = []
        for recognizer in self.recognizers:
            try:
                hits.extend(recognizer.analyze(text))
            except Exception:  # noqa: BLE001
                # One broken recognizer must not disable the whole gate, but it
                # must be loud: a silent detector is an undetected leak.
                logger.exception(
                    "recognizer failed",
                    extra={"recognizer": getattr(recognizer, "name", "unknown")},
                )
        admitted = [h for h in hits if h.score >= self.min_score]
        return resolve_conflicts(admitted)

    def mask(self, text: str, *, session_id: str) -> MaskingResult:
        """Replace every detected entity with a placeholder (FR-1.2)."""
        settings = get_settings()
        started = time.perf_counter()

        vault: SessionVault = registry.get_or_create(session_id)
        allocator = self._allocators.setdefault(
            session_id, _Allocator(key=hashlib.sha256(session_id.encode()).digest())
        )

        entities = self.detect(text)

        # Rebuild the string left to right so offsets stay meaningful.
        pieces: list[str] = []
        placeholders: dict[str, str] = {}
        cursor = 0
        for entity in entities:
            placeholder, is_new = allocator.allocate(entity.entity_type, entity.text)
            if is_new:
                vault.store(placeholder, entity.text)
            pieces.append(text[cursor : entity.start])
            pieces.append(placeholder)
            placeholders[placeholder] = entity.entity_type.value
            cursor = entity.end
        pieces.append(text[cursor:])

        duration_ms = (time.perf_counter() - started) * 1000.0
        within_budget = duration_ms <= settings.pii_latency_budget_ms

        if not within_budget:
            # NFR-2.1 is a budget, not a hard failure: refusing to mask would be
            # worse than masking slowly. Breaches are recorded so the regression
            # stays visible rather than being absorbed.
            logger.warning(
                "PII masking exceeded latency budget",
                extra={
                    "duration_ms": round(duration_ms, 2),
                    "budget_ms": settings.pii_latency_budget_ms,
                    "entity_count": len(entities),
                },
            )

        return MaskingResult(
            masked_text="".join(pieces),
            original_length=len(text),
            entities=tuple(entities),
            placeholders=placeholders,
            duration_ms=duration_ms,
            session_id=session_id,
            within_budget=within_budget,
        )

    def remask(self, text: str, *, session_id: str) -> str:
        """Re-apply this session's placeholders to text that holds real values.

        The inverse of unmask, and needed because values legitimately travel
        unmasked *inside* the trust boundary — the tools query the ERP with
        real CR numbers, so a recorded tool argument holds one. Projecting
        that straight into an HTTP response would hand an original value to a
        reader who never passed the pii:reveal check and left no disclosure
        record, quietly routing around the one audited door onto that data.

        Longest originals are substituted first so that one value which is a
        substring of another cannot be partially rewritten.
        """
        vault = registry.get(session_id)
        if vault is None:
            return text

        pairs: list[tuple[str, str]] = []
        for placeholder in vault.placeholders():
            original = vault.resolve(placeholder)
            if original:
                pairs.append((original, placeholder))

        rewritten = text
        for original, placeholder in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
            rewritten = rewritten.replace(original, placeholder)
        return rewritten

    def unmask(self, text: str, *, session_id: str) -> str:
        """Restore original values for display to an authorised caller (FR-1.3).

        Authorisation is NOT checked here - app.pii.disclosure.reveal is the
        only supported entry point. Keeping the permission check out of the
        engine avoids two half-enforced paths to the same data.
        """
        vault = registry.get(session_id)
        if vault is None:
            return text

        restored = text
        for placeholder in sorted(vault.placeholders(), key=len, reverse=True):
            if placeholder not in restored:
                continue
            original = vault.resolve(placeholder)
            if original is not None:
                restored = restored.replace(placeholder, original)
        return restored


_engine: MaskingEngine | None = None


def get_engine() -> MaskingEngine:
    """Process-wide engine. Recognizers are stateless, so sharing is safe."""
    global _engine
    if _engine is None:
        _engine = MaskingEngine()
    return _engine
