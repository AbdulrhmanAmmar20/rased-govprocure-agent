"""Phone-number and email recognizers (FR-1.1)."""

from __future__ import annotations

import re

from app.pii.entities import DetectedEntity, EntityType

# Saudi numbering plan:
#   mobile   05X XXX XXXX / +9665X XXX XXXX / 009665X...
#   landline 01X XXX XXXX (011 Riyadh, 012 Makkah, 013 Eastern, ...)
# The alternation is ordered longest-first so the international form is not
# truncated to its national tail.
_PHONE = re.compile(
    r"""(?<![\d\w])(
          (?:\+966|00966)[\s\-]?5[\d][\s\-]?[\d]{3}[\s\-]?[\d]{4}
        | (?:\+966|00966)[\s\-]?1[\d][\s\-]?[\d]{3}[\s\-]?[\d]{4}
        | 05[\d][\s\-]?[\d]{3}[\s\-]?[\d]{4}
        | 01[\d][\s\-]?[\d]{3}[\s\-]?[\d]{4}
    )(?![\d])""",
    re.VERBOSE,
)

_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)

_PHONE_CONTEXT = ("جوال", "هاتف", "تواصل", "رقم", "phone", "mobile", "tel")
_CONTEXT_WINDOW = 32


class PhoneRecognizer:
    """Detects Saudi mobile and landline numbers in national or E.164 form."""

    name = "phone_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        found: list[DetectedEntity] = []
        lowered = text.lower()

        for match in _PHONE.finditer(text):
            raw = match.group(1)
            window = lowered[
                max(0, match.start() - _CONTEXT_WINDOW) : match.end() + _CONTEXT_WINDOW
            ]
            has_context = any(word in window for word in _PHONE_CONTEXT)
            found.append(
                DetectedEntity(
                    entity_type=EntityType.PHONE,
                    start=match.start(1),
                    end=match.end(1),
                    text=raw,
                    score=0.90 if has_context else 0.78,
                    recognizer=self.name,
                    validated=False,
                )
            )
        return found


class EmailRecognizer:
    """Detects email addresses.

    The pattern is intentionally conservative: an address is unambiguous
    enough that a shape match is a determination, so these are marked
    validated and outrank a bare digit run they might overlap.
    """

    name = "email_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        return [
            DetectedEntity(
                entity_type=EntityType.EMAIL,
                start=match.start(),
                end=match.end(),
                text=match.group(0),
                score=0.95,
                recognizer=self.name,
                validated=True,
            )
            for match in _EMAIL.finditer(text)
        ]
