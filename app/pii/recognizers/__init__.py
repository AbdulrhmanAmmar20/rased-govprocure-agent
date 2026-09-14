"""In-tree regex recognizers.

These are the ones that must always work, on any machine, with no model
download and no network — the guarantee FR-1 depends on. The optional
Presidio adapter layers on top of them rather than replacing them.
"""

from __future__ import annotations

from app.pii.entities import Recognizer
from app.pii.recognizers.commercial import CRNumberRecognizer, IBANRecognizer
from app.pii.recognizers.contact import EmailRecognizer, PhoneRecognizer
from app.pii.recognizers.identity import SaudiIdentityRecognizer
from app.pii.recognizers.names import OrganizationRecognizer, PersonNameRecognizer

__all__ = [
    "CRNumberRecognizer",
    "EmailRecognizer",
    "IBANRecognizer",
    "OrganizationRecognizer",
    "PersonNameRecognizer",
    "PhoneRecognizer",
    "SaudiIdentityRecognizer",
    "default_recognizers",
]


def default_recognizers() -> list[Recognizer]:
    """The standard battery.

    Order is not significant for correctness — every recognizer sees the full
    original text and conflicts are settled afterwards by
    :func:`app.pii.entities.resolve_conflicts` — but the strongest, checksum
    backed detectors are listed first to keep the intent obvious.
    """
    return [
        SaudiIdentityRecognizer(),
        IBANRecognizer(),
        CRNumberRecognizer(),
        PhoneRecognizer(),
        EmailRecognizer(),
        PersonNameRecognizer(),
        OrganizationRecognizer(),
    ]
