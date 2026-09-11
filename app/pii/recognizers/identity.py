"""National ID and Iqama recognizer (FR-1.1).

Both are ten digits, and so are commercial registrations. Separation is done
by the Luhn check digit first and Arabic context words second, never by
position in the sentence.
"""

from __future__ import annotations

import re

from app.pii.entities import DetectedEntity, EntityType
from app.pii.validators import is_valid_saudi_id, saudi_id_class

# \d matches Arabic-Indic numerals too, so spans are found in the original
# text and offsets stay valid against the string the caller passed in.
_TEN_DIGITS = re.compile(r"(?<![\d\w])([\d]{10})(?![\d])")

# Context words that raise confidence when they sit near the match.
_ID_CONTEXT = (
    "هوية",
    "الهوية",
    "هويه",
    "الهويه",
    "وطنية",
    "الوطنية",
    "إقامة",
    "اقامة",
    "الإقامة",
    "الاقامة",
    "مقيم",
    "national id",
    "iqama",
    "identity",
)

_CONTEXT_WINDOW = 40


class SaudiIdentityRecognizer:
    """Detects Saudi National ID (leading 1) and Iqama (leading 2) numbers."""

    name = "saudi_identity_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        found: list[DetectedEntity] = []
        lowered = text.lower()

        for match in _TEN_DIGITS.finditer(text):
            raw = match.group(1)
            if not is_valid_saudi_id(raw):
                continue

            holder = saudi_id_class(raw)
            entity_type = EntityType.SAUDI_ID if holder == "citizen" else EntityType.IQAMA

            window = lowered[
                max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW
            ]
            has_context = any(word in window for word in _ID_CONTEXT)

            # A valid checksum is already strong evidence; context lifts it to
            # near-certainty. Even the floor stays above the engine's accept
            # threshold, because a false positive here costs a redacted number
            # while a false negative costs a disclosed identity.
            found.append(
                DetectedEntity(
                    entity_type=entity_type,
                    start=match.start(1),
                    end=match.end(1),
                    text=raw,
                    score=0.95 if has_context else 0.80,
                    recognizer=self.name,
                    validated=True,
                )
            )
        return found
