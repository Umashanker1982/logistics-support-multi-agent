"""PII redaction applied before anything is written to logs or traces."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def redact_pii(text: str) -> str:
    """Mask email addresses, card-like numbers, and phone numbers."""
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CARD.sub("[CARD]", text)
    text = _PHONE.sub("[PHONE]", text)
    return text
