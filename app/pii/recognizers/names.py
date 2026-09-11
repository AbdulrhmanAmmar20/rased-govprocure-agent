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
from app.pii.validators import normalize_arabic

_ARABIC_WORD = r"[ء-ٰٕٱ-ۓ]+"

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

# Compared in folded form so that a stop-word carrying tanween or a hamza
# variant still terminates the span.
_FOLDED_STOP_WORDS = frozenset(normalize_arabic(w) for w in _STOP_WORDS)

# Proclitics that attach to the front of an Arabic word: the conjunctions و/ف
# and the prepositions ب/ك/ل, optionally followed by the definite article ال.
_PROCLITICS = ("و", "ف", "ب", "ك", "ل")
_ARTICLE = "ال"

# Below this length, stripping a prefix does more harm than good - it would
# turn the three-letter name بدر into در and stop a span that should continue.
_MIN_STEM_LENGTH = 4

_MAX_NAME_WORDS = 4


def _stop_word_variants(word: str) -> set[str]:
    """Every form of ``word`` that should be checked against the stop list.

    Arabic attaches clitics directly to the word, so the stop-word سجل turns
    up in real text as بالسجل, والسجل or للسجل, and the accusative tanween
    turns عرض into عرضا. A plain set membership test misses all of those and
    the span runs on into the following clause, over-masking the sentence.
    """
    folded = normalize_arabic(word)
    variants = {folded}

    stem = folded
    for _ in range(2):  # at most one proclitic plus the article
        changed = False
        if len(stem) > _MIN_STEM_LENGTH and stem[0] in _PROCLITICS:
            stem = stem[1:]
            variants.add(stem)
            changed = True
        if len(stem) > _MIN_STEM_LENGTH and stem.startswith(_ARTICLE):
            stem = stem[len(_ARTICLE) :]
            variants.add(stem)
            changed = True
        if not changed:
            break

    # Accusative ending: عرضا -> عرض
    for candidate in list(variants):
        if len(candidate) > 3 and candidate.endswith("ا"):
            variants.add(candidate[:-1])

    return variants


def _is_stop_word(word: str) -> bool:
    return bool(_stop_word_variants(word) & _FOLDED_STOP_WORDS)


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
        if _is_stop_word(word):
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
