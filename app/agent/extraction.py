"""Pull structured facts out of an Arabic request (inside the trust boundary).

This runs on the original text, before masking removes the identifiers, and
its output feeds the tools — which need real CR numbers to query the ERP.
That is not a gap in FR-1: masking protects the boundary with the inference
engine, and this code sits on the application's side of it.

Parsing is deliberately conservative. A misread amount would be worse than no
amount at all, because the ceiling check is arithmetic and would silently
compare the wrong number. Anything not confidently parsed is left as None and
the caller is expected to ask rather than guess.
"""

from __future__ import annotations

import re

from app.agent.state import TransactionFacts
from app.pii.entities import EntityType
from app.pii.validators import normalize_arabic, normalize_digits

# Multipliers written as words after the figure: "120 ألف ريال".
_MULTIPLIERS = {
    "الف": 1_000,
    "الاف": 1_000,
    "مليون": 1_000_000,
    "ملايين": 1_000_000,
    "مليار": 1_000_000_000,
}

_AMOUNT = re.compile(
    r"(?P<figure>[\d][\d,٫٬\.]*)\s*"
    r"(?P<multiplier>[ء-ي]+)?\s*"
    r"(?:ريال|ر\.س|SAR|sar)",
)

_PERCENT = re.compile(r"(?P<value>[\d]+(?:[\.,][\d]+)?)\s*(?:%|بالمئة|في المئة|بالمائة)")

_LOCAL_CONTENT_HINT = ("محتوي محلي", "المحتوي المحلي", "محتوى محلي", "المحتوى المحلي")

_DEPARTMENT = re.compile(r"\b([A-Z]{2,10}-(?:DEPT|DEP|DIV))\b")


def _to_number(figure: str) -> float | None:
    """Parse a figure that may carry Arabic decimal or thousands separators."""
    cleaned = normalize_digits(figure)
    # U+066C is the Arabic thousands separator; U+066B is the decimal one.
    cleaned = cleaned.replace("٬", "").replace(",", "")
    cleaned = cleaned.replace("٫", ".")
    if cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_amount_sar(text: str) -> float | None:
    """The contract value in SAR, or None when it cannot be read confidently.

    When several amounts appear the largest is taken: a request that mentions
    both a unit price and a total is describing a contract worth the total,
    and under-reading it would understate the transaction against the ceiling.
    """
    candidates: list[float] = []
    for match in _AMOUNT.finditer(text):
        value = _to_number(match.group("figure"))
        if value is None:
            continue
        multiplier_word = match.group("multiplier")
        if multiplier_word:
            folded = normalize_arabic(multiplier_word)
            multiplier = _MULTIPLIERS.get(folded)
            if multiplier is None:
                # An unrecognised word between figure and currency means the
                # match is probably not an amount at all. Skip it.
                continue
            value *= multiplier
        candidates.append(value)
    return max(candidates) if candidates else None


def extract_local_content_ratio(text: str) -> float | None:
    """A percentage stated near a local-content phrase, as a 0-1 ratio."""
    folded = normalize_arabic(text)
    if not any(hint in folded for hint in (normalize_arabic(h) for h in _LOCAL_CONTENT_HINT)):
        return None
    match = _PERCENT.search(normalize_digits(text))
    if not match:
        return None
    value = _to_number(match.group("value"))
    if value is None:
        return None
    return value / 100.0 if value > 1 else value


def extract_department(text: str, fallback: str = "") -> str:
    match = _DEPARTMENT.search(text)
    return match.group(1) if match else fallback


def extract_facts(
    text: str,
    *,
    detected_entities: tuple = (),
    department_fallback: str = "",
) -> TransactionFacts:
    """Assemble the facts the compliance checks need.

    The vendor CR is taken from the masking engine's detections rather than
    re-parsed here, so exactly one component decides what counts as a CR
    number and the tools can never be handed something the masker did not
    recognise.
    """
    vendor_cr: str | None = None
    for entity in detected_entities:
        if entity.entity_type is EntityType.CR_NUMBER:
            vendor_cr = normalize_digits(entity.text)
            break

    return TransactionFacts(
        amount_sar=extract_amount_sar(text),
        vendor_cr=vendor_cr,
        department_id=extract_department(text, department_fallback),
        subject_matter=text.strip()[:160],
        local_content_ratio=extract_local_content_ratio(text),
    )
