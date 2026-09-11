"""Checksum and format validators.

Shape matching alone is not enough here: a ten-digit run in an Arabic sentence
could be a national ID, a commercial registration, a budget code or a date.
Validating the check digit is what turns a guess into a determination, and it
is what the conflict resolver keys on.
"""

from __future__ import annotations

# Arabic-Indic (٠-٩) and Extended Arabic-Indic (۰-۹) digits map onto ASCII.
# Arabic-language forms routinely carry these, and an unnormalised pipeline
# would mask the ASCII spelling of an ID while letting the Arabic one through.
_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

# Separators people type inside long numbers: 1010-99-8877, SA03 8000 0000...
_SEPARATORS = str.maketrans("", "", " -_ ‏‎")


def normalize_digits(value: str) -> str:
    """Fold Arabic-Indic digits to ASCII, leaving everything else untouched."""
    return value.translate(_DIGIT_TRANSLATION)


def strip_separators(value: str) -> str:
    """Remove spaces, dashes and bidi marks from inside a numeric token."""
    return value.translate(_SEPARATORS)


def is_valid_saudi_id(value: str) -> bool:
    """Validate a 10-digit National ID or Iqama number.

    The Kingdom's identity numbers carry a Luhn check digit. The leading digit
    encodes the holder class: 1 for a Saudi national, 2 for a resident.
    """
    digits = strip_separators(normalize_digits(value))
    if len(digits) != 10 or not digits.isdigit():
        return False
    if digits[0] not in {"1", "2"}:
        return False

    total = 0
    for index, char in enumerate(digits[:9]):
        digit = int(char)
        if index % 2 == 0:  # double the odd positions (1st, 3rd, 5th, ...)
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(digits[9])


def saudi_id_class(value: str) -> str:
    """``"citizen"`` for a 1-prefixed number, ``"resident"`` for a 2-prefixed one."""
    digits = strip_separators(normalize_digits(value))
    return {"1": "citizen", "2": "resident"}.get(digits[:1], "unknown")


# Commercial-registration region prefixes issued by the Ministry of Commerce.
# A ten-digit number opening with one of these is a CR far more often than it
# is anything else, which is the signal used when no checksum is available.
CR_REGION_PREFIXES: frozenset[str] = frozenset(
    {
        "1010",  # الرياض
        "1011",  # الرياض - فرعي
        "2050",  # الدمام
        "2051",  # الدمام - فرعي
        "2052",  # الأحساء
        "3350",  # المدينة المنورة
        "4030",  # جدة
        "4031",  # جدة - فرعي
        "4650",  # الطائف
        "4700",  # مكة المكرمة
        "5850",  # أبها
        "5855",  # خميس مشيط
        "5900",  # جازان
        "6050",  # حائل
        "7001",  # الجوف
        "7002",  # تبوك
        "7003",  # نجران
        "7004",  # الباحة
    }
)

# The Unified National Number for establishments opens with 7 and is 10 long.
UNIFIED_NUMBER_PREFIX = "7"


def looks_like_cr_number(value: str) -> bool:
    """True when a 10-digit token carries a recognised CR or unified prefix."""
    digits = strip_separators(normalize_digits(value))
    if len(digits) != 10 or not digits.isdigit():
        return False
    return digits[:4] in CR_REGION_PREFIXES or digits.startswith(UNIFIED_NUMBER_PREFIX)


def is_valid_iban(value: str) -> bool:
    """ISO 13616 mod-97 validation, constrained to Saudi IBANs (SA, 24 chars)."""
    candidate = strip_separators(normalize_digits(value)).upper()
    if len(candidate) != 24 or not candidate.startswith("SA"):
        return False
    if not candidate[2:].isalnum():
        return False

    rearranged = candidate[4:] + candidate[:4]
    numeric = "".join(
        str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged
    )
    if not numeric.isdigit():
        return False
    return int(numeric) % 97 == 1


def iban_bank_code(value: str) -> str:
    """The two-digit bank identifier inside a Saudi IBAN (positions 5-6)."""
    candidate = strip_separators(normalize_digits(value)).upper()
    return candidate[4:6] if len(candidate) == 24 else ""


# Arabic diacritics (tashkeel), tatweel and the superscript alef. These are
# invisible to a reader but are distinct code points, so a stop-word list or a
# gazetteer compared without folding them silently stops matching.
_TASHKEEL = str.maketrans(
    "",
    "",
    "ًٌٍَُِّْ"
    "ٰٕٓٔـ",
)

# Orthographic variants of the same letter.
_LETTER_FOLDING = str.maketrans("أإآٱى", "ااااي")


def strip_tashkeel(value: str) -> str:
    """Drop diacritics and tatweel, leaving the consonantal skeleton."""
    return value.translate(_TASHKEEL)


def normalize_arabic(value: str) -> str:
    """Fold a word to a comparison form.

    Removes diacritics and unifies the alef and alef-maqsura variants, so that
    a token written as عرضاً, عرضا or عَرْضًا all compare equal to عرضا.
    Used only for matching; spans are always reported against the original
    text so masking offsets stay exact.
    """
    return strip_tashkeel(value).translate(_LETTER_FOLDING)
