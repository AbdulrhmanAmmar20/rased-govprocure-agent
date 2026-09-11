"""Person-name and organisation-name recognizers (FR-1.1).

Names are the one class in FR-1.1 with no checksum and no fixed shape, so
detection is trigger-driven: an Arabic honorific or a legal-form word opens a
span, and a stop-word closes it. This is deliberately high-precision and
low-recall. Presidio's NER, when installed, is what raises recall — see
:mod:`app.pii.presidio_adapter`. The regex layer's job is to guarantee a
floor that needs no model to be present.
"""

from __future__ import annotations

import re

from app.pii.entities import DetectedEntity, EntityType

_ARABIC_WORD = r"[ء-يٱ-ۓ]+"

# Honorifics and role words that reliably precede a personal name.
_PERSON_TRIGGERS = (
    "الأستاذ", "الاستاذ", "الاستاذة", "الأستاذة",
    "المهندس", "المهندسة", "الدكتور", "الدكتورة",
    "السيد", "السيدة", "الشيخ", "المدعو", "المفوض", "المندوب",
    "باسم", "بإسم",
)

# Legal forms that precede an establishment's trade name.
_ORG_TRIGGERS = (
    "مؤسسة", "مؤسسه", "شركة", "شركه", "مصنع", "مكتب",
    "مجموعة", "مجموعه", "مركز", "وكالة", "وكاله", "متجر", "معهد",
)

# Words that cannot be part of a name and therefore end the span.
_STOP_WORDS = frozenset(
    {
        "سجل", "تجاري", "التجاري", "رقم", "برقم", "بمبلغ", "مبلغ", "بقيمة",
        "قيمة", "ريال", "وفق", "حسب", "ضمن", "بتاريخ", "في", "على", "من",
        "إلى", "الى", "و", "أو", "او", "نظام", "المنافسات", "المشتريات",
        "الحكومية", "العقد", "بعقد", "لتوريد", "لشراء", "للتوريد", "عرض",
        "العرض", "المنافسة", "منافسة", "هوية", "الهوية", "إقامة", "الاقامة",
        "جوال", "هاتف", "البريد", "بنسبة", "نسبة", "المحتوى", "المحلي",
    }
)

_MAX_NAME_WORDS = 4


def _span_after_trigger(text: str, trigger_end: int) -> tuple[int, int] | None:
    """Return the (start, end) span of the name following a trigger word."""
    cursor = trigger_end
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1

    start = cursor
    end = cursor
    words = 0

    while words < _MAX_NAME_WORDS:
        match = re.compile(_ARABIC_WORD).match(text, cursor)
        if not match:
            break
        word = match.group(0)
        if word in _STOP_WORDS:
            break

        end = match.end()
        words += 1
        cursor = match.end()
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1

    if words == 0 or end <= start:
        return None
    return start, end


def _build(triggers: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(sorted(triggers, key=len, reverse=True))
    return re.compile(rf"(?<![ء-ي])({alternation})(?![ء-ي])")


_PERSON_PATTERN = _build(_PERSON_TRIGGERS)
_ORG_PATTERN = _build(_ORG_TRIGGERS)


class PersonNameRecognizer:
    """Detects personal names introduced by an Arabic honorific or role word.

    The trigger itself is left in place — masking «المهندس» would strip
    meaning the auditor needs while protecting nothing.
    """

    name = "person_name_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        found: list[DetectedEntity] = []
        for match in _PERSON_PATTERN.finditer(text):
            span = _span_after_trigger(text, match.end())
            if span is None:
                continue
            start, end = span
            found.append(
                DetectedEntity(
                    entity_type=EntityType.PERSON,
                    start=start,
                    end=end,
                    text=text[start:end],
                    score=0.75,
                    recognizer=self.name,
                    validated=False,
                )
            )
        return found


class OrganizationRecognizer:
    """Detects establishment trade names introduced by a legal-form word.

    A vendor's identity is commercially sensitive under the project objective,
    so it is masked alongside personal data. The legal form («مؤسسة») stays
    visible so the model still knows it is reasoning about an establishment.
    """

    name = "organization_regex"

    def analyze(self, text: str) -> list[DetectedEntity]:
        found: list[DetectedEntity] = []
        for match in _ORG_PATTERN.finditer(text):
            span = _span_after_trigger(text, match.end())
            if span is None:
                continue
            start, end = span
            found.append(
                DetectedEntity(
                    entity_type=EntityType.ORGANIZATION,
                    start=start,
                    end=end,
                    text=text[start:end],
                    score=0.72,
                    recognizer=self.name,
                    validated=False,
                )
            )
        return found
