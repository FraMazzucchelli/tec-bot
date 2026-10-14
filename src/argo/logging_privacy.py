"""Log senza numeri di telefono in chiaro (minimizzazione dei dati, GDPR art. 5).

I messaggi di errore di Twilio e gli identificativi di sessione possono contenere
numeri di telefono: prima di scriverli nei log li mascheriamo, lasciando solo il
prefisso internazionale (es. +39*******).
"""

from __future__ import annotations

import logging
import re

_PHONE = re.compile(r"(\+\d{2})\d{5,}")


def mask_phones(text: str) -> str:
    return _PHONE.sub(lambda m: m.group(1) + "*" * 7, text)


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask_phones(super().format(record))


def install(level: int = logging.INFO) -> None:
    """Configura il logging dell'app con il mascheramento dei numeri."""
    logging.basicConfig(level=level)
    formatter = MaskingFormatter("%(levelname)s:%(name)s:%(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)
