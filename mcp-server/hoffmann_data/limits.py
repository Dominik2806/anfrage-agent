"""Eingabeprüfung der Lese-Werkzeuge (F07, Auftrag 11.3: Eingaben begrenzen).

Jede Prüfung gibt den bereinigten Wert zurück oder wirft einen ToolError mit fester Meldung. Die Meldung
enthält nie den Eingabewert: Kundentext ist Daten, kein Echo (Auftrag 5). Die Werkzeuge rufen die Prüfungen
auf, bevor sie die Datenbank berühren; ein ungültiger Wert erreicht die Datenbank nie.

Regeln (nach strip()):
- Suchtext und Kundenangabe: 1 bis 200 Zeichen (Zeichen, nicht Bytes). Abgelehnt wird jedes Zeichen mit der
  Unicode-Kategorie C* (Cc, Cf, Cs, Co, Cn) sowie Zl und Zp. Damit sind Steuerzeichen, NUL, einzelne
  Surrogate, Bidi-Überschreibungen, Zeilen- und Absatztrenner ausgeschlossen.
- Limit: nur ein echtes int von 1 bis 20. Kein bool, kein Text, keine Kommazahl, kein null.
- Artikelnummer: Nach strip() muss die Eingabe reines ASCII sein, erst dann folgen upper() und der Regex
  (fullmatch). Sonst würden Zeichen wie lange s, i ohne Punkt oder die ff-Ligatur von upper() zu
  gültigen Buchstaben. Das Format entspricht dem CHECK in db/schema.sql.
"""

import re
import unicodedata
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

MAX_QUERY_LENGTH = 200
DEFAULT_LIMIT = 5
MAX_LIMIT = 20

QUERY_INVALID = (
    "Der Suchtext ist ungültig: Er muss 1 bis 200 Zeichen lang sein und darf keine Steuer-, "
    "Format- oder Trennzeichen enthalten."
)
LIMIT_INVALID = "Das Limit ist ungültig: Erlaubt ist eine ganze Zahl von 1 bis 20."
ARTICLE_NUMBER_INVALID = (
    "Die Artikelnummer ist ungültig: Erwartet wird das Format AB-1234 oder AB-1234-X1."
)
CUSTOMER_QUERY_INVALID = (
    "Die Kundenangabe ist ungültig: Erwartet werden Firmenname, E-Mail-Adresse oder Domain mit "
    "1 bis 200 Zeichen, ohne Steuer-, Format- oder Trennzeichen."
)

_ARTICLE_NUMBER = re.compile(r"[A-Z]{2}-[0-9]{4}(-[A-Z0-9]{1,4})?")


def _is_rejected_character(character: str) -> bool:
    category = unicodedata.category(character)
    return category.startswith("C") or category in ("Zl", "Zp")


def _check_text(value: Any, message: str) -> str:
    if not isinstance(value, str):
        raise ToolError(message)
    text = value.strip()
    if not 1 <= len(text) <= MAX_QUERY_LENGTH:
        raise ToolError(message)
    if any(_is_rejected_character(character) for character in text):
        raise ToolError(message)
    return text


def check_query(value: Any) -> str:
    """Suchtext der Produktsuche: der bereinigte Text oder ToolError(QUERY_INVALID)."""
    return _check_text(value, QUERY_INVALID)


def check_customer_query(value: Any) -> str:
    """Firma, E-Mail-Adresse oder Domain: der bereinigte Text oder ToolError(CUSTOMER_QUERY_INVALID)."""
    return _check_text(value, CUSTOMER_QUERY_INVALID)


def check_limit(value: Any) -> int:
    """Trefferzahl: nur ein echtes int von 1 bis MAX_LIMIT, sonst ToolError(LIMIT_INVALID)."""
    # type() statt isinstance(): bool ist eine Unterklasse von int, true darf kein Limit sein
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise ToolError(LIMIT_INVALID)
    return value


def check_article_number(value: Any) -> str:
    """Artikelnummer in Großbuchstaben oder ToolError(ARTICLE_NUMBER_INVALID)."""
    if not isinstance(value, str):
        raise ToolError(ARTICLE_NUMBER_INVALID)
    text = value.strip()
    # Erst ASCII prüfen, dann upper(): upper() macht aus Nicht-ASCII-Zeichen ASCII-Buchstaben
    if not text.isascii():
        raise ToolError(ARTICLE_NUMBER_INVALID)
    text = text.upper()
    if not _ARTICLE_NUMBER.fullmatch(text):
        raise ToolError(ARTICLE_NUMBER_INVALID)
    return text
