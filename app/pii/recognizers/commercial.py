"""Commercial-registration and IBAN recognizers (FR-1.1).

These carry the commercially sensitive half of the objective: a vendor's CR
number and banking details are exactly what must not leak out of the entity
through a model call.
"""

from __future__ import annotations

import re

from app.pii.entities import DetectedEntity, EntityType
from app.pii.validators import is_valid_iban, looks_like_cr_number, strip_separators

_TEN_DIGITS = re.compile(r"(?<![\d\w])([\d]{10})(?![\d])")

# SA + 22 alphanumerics, tolerating the spaces and dashes people type between
# groups. Validation re-joins them before the mod-97 check.
_IBAN = re.compile(r"\bSA(?:[ \-]?[0-9A-Za-z]){22}\b", re.IGNORECASE)

_CR_CONTEXT = (
    "سجل تجاري",
    "السجل التجاري",
    "سجلها التجاري",
    "سجله التجاري",
    "س.ت",
    "الرقم الموحد",
    "رقم موحد",
    "commercial registration",
    "cr number",
    "cr no",
    " cr ",
)

_IBAN_CONTEXT = ("ايبان", "آيبان", "الآيبان", "حساب", "الحساب", "بنك", "iban", "account")

_CONTEXT_WINDOW = 48


class CRNumberRecognizer:
    """Detects commercial-registration and unified establishment numbers.

    A CR has no check digit, so certainty has to come from somewhere else:
    either a recognised Ministry of Commerce region prefix, or an explicit
    Arabic context phrase such as «سجل تجاري».
    """

    name = "cr_number_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        found: list[DetectedEntity] = []
        lowered = text.lower()

        for match in _TEN_DIGITS.finditer(text):
            raw = match.group(1)
            window = lowered[
                max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW
            ]
            has_context = any(phrase in window for phrase in _CR_CONTEXT)
            has_prefix = looks_like_cr_number(raw)

            if not (has_context or has_prefix):
                continue

            if has_context and has_prefix:
                score = 0.95
            elif has_context:
                score = 0.85
            else:
                score = 0.70

            found.append(
                DetectedEntity(
                    entity_type=EntityType.CR_NUMBER,
                    start=match.start(1),
                    end=match.end(1),
                    text=raw,
                    score=score,
                    recognizer=self.name,
                    # A prefix match is a strong structural signal, but it is
                    # not a checksum, so identity hits still outrank it.
                    validated=False,
                )
            )
        return found


class IBANRecognizer:
    """Detects Saudi IBANs, admitted only on a passing ISO 13616 mod-97 check."""

    name = "iban_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        found: list[DetectedEntity] = []
        lowered = text.lower()

        for match in _IBAN.finditer(text):
            raw = match.group(0)
            if not is_valid_iban(strip_separators(raw)):
                continue

            window = lowered[
                max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW
            ]
            has_context = any(word in window for word in _IBAN_CONTEXT)

            found.append(
                DetectedEntity(
                    entity_type=EntityType.IBAN,
                    start=match.start(),
                    end=match.end(),
                    text=raw,
                    score=0.98 if has_context else 0.92,
                    recognizer=self.name,
                    validated=True,
                )
            )
        return found
