"""Entity taxonomy for the masking engine (FR-1.1).

``placeholder_tag`` is what the LLM actually sees. The SRS fixes two of these
by name — ``<SAUDI_ID_1>`` and ``<CR_NUM_1>`` — and the rest follow the same
shape so a reviewer reading a masked prompt can tell at a glance what class of
value was removed without being able to recover it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class EntityType(StrEnum):
    SAUDI_ID = "SAUDI_ID"
    IQAMA = "IQAMA"
    CR_NUMBER = "CR_NUM"
    IBAN = "IBAN"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    PERSON = "PERSON"
    ORGANIZATION = "ORG"

    @property
    def placeholder_tag(self) -> str:
        return self.value

    @property
    def arabic_label(self) -> str:
        return _ARABIC_LABELS[self]


_ARABIC_LABELS: dict[EntityType, str] = {
    EntityType.SAUDI_ID: "رقم الهوية الوطنية",
    EntityType.IQAMA: "رقم الإقامة",
    EntityType.CR_NUMBER: "رقم السجل التجاري",
    EntityType.IBAN: "رقم الآيبان البنكي",
    EntityType.PHONE: "رقم الهاتف",
    EntityType.EMAIL: "البريد الإلكتروني",
    EntityType.PERSON: "اسم شخصي",
    EntityType.ORGANIZATION: "اسم منشأة",
}


@dataclass(frozen=True, slots=True)
class DetectedEntity:
    """One span of the input that must not reach the model."""

    entity_type: EntityType
    start: int
    end: int
    text: str
    score: float
    recognizer: str
    validated: bool = False
    """True when a checksum confirmed the value, not merely a shape match.

    Validated hits win conflicts against unvalidated ones — this is how a real
    national ID is told apart from a CR number of the same length.
    """

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: DetectedEntity) -> bool:
        return self.start < other.end and other.start < self.end


@runtime_checkable
class Recognizer(Protocol):
    """Contract every detector implements.

    Kept structural so the optional Presidio adapter can drop in alongside the
    in-tree regex recognizers without inheriting from anything.
    """

    name: str

    def analyze(self, text: str) -> list[DetectedEntity]: ...


def resolve_conflicts(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Collapse overlapping detections into one non-overlapping, sorted list.

    Precedence, highest first:
      1. checksum-validated over shape-only,
      2. higher confidence,
      3. longer span (prefer masking the full IBAN over a digit run inside it).

    Dropping a span here means leaking it, so ties resolve toward masking more.
    """
    ordered = sorted(
        entities,
        key=lambda e: (e.validated, e.score, e.length),
        reverse=True,
    )
    kept: list[DetectedEntity] = []
    for candidate in ordered:
        if not any(candidate.overlaps(k) for k in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda e: e.start)
