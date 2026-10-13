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

Schreib-Werkzeuge create_lead und log_activity (F08, ADR 0005): Jede Funktion prüft in dieser Reihenfolge den Typ
(nur str), entfernt die Ränder, prüft die Länge in Zeichen und dann die Zeichenklassen.
- Firmenname 1 bis 200, Vorname und Nachname je 1 bis 100 Zeichen, Position höchstens 100 (fehlt sie oder ist sie
  leer: None). Diese Felder sind einzeilig: Abgelehnt wird jedes Zeichen mit derselben Regel wie beim Suchtext
  (C*, Zl, Zp, auch Zeilenumbruch und Tab).
- Betreff 1 bis 200, Zusammenfassung 1 bis 2000 Zeichen. Erlaubt sind Zeilenumbruch, Wagenrücklauf und Tab; alle
  anderen Zeichen der Kategorien C* sowie Zl und Zp werden abgelehnt (NUL kann PostgreSQL nicht speichern).
- E-Mail höchstens 254 Zeichen, genau ein @, Lokalteil und Domain nicht leer, kein Leerraum, keine Steuer-, Format-
  und Trennzeichen. Die Domain entspricht den CHECKs von customers.domain und contacts.email in db/schema.sql
  (nur ASCII-Labels aus [a-z0-9-], endet auf .example, keine leeren Labels; Domain höchstens 253 Zeichen).
  Auch der Teil vor dem @ muss reines ASCII sein (Großbuchstaben sind erlaubt und werden kleingeschrieben).
- Sprache, Land, Branche und Aktivitätstyp: nur die Werte des Schemas (der Aktivitätstyp nur die drei, die die Rolle
  data_service_rw schreiben darf).

Unterschied beim Bereinigen: Die Aufzählungen werden exakt verglichen, ohne strip und ohne lower. Es sind
Schlüsselwörter, die der Aufrufer genau so schreiben muss, wie das Schema sie kennt; eine stille Korrektur ("DE",
" de") würde Fehler des Aufrufers verdecken. E-Mail und Domain werden dagegen bereinigt und klein zurückgegeben,
weil das Schema email = lower(email) und domain = lower(domain) verlangt und Absender ihre Adresse beliebig
schreiben. Die Domain und der Teil der E-Mail-Adresse vor dem @ müssen vorher reines ASCII sein: lower() würde sonst
Zeichen wie das Kelvin-Zeichen in ein ASCII-k verwandeln und eine fremde Schreibweise stillschweigend gültig machen,
und das lower() von PostgreSQL hängt von der Locale der Datenbank ab.
Die Domainregel hier (_SCHEMA_DOMAIN) ist die strenge Regel des Schreibens; die lockere Regel der Suche in crm.py
(find_customer) ist eine andere und bleibt unverändert.
"""

import re
import unicodedata
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

MAX_QUERY_LENGTH = 200
DEFAULT_LIMIT = 5
MAX_LIMIT = 20

# Schreib-Werkzeuge (F08). Die Längen sind Vorgaben, das Schema begrenzt die text-Spalten nicht.
MAX_COMPANY_NAME_LENGTH = 200
MAX_PERSON_NAME_LENGTH = 100
MAX_JOB_TITLE_LENGTH = 100
MAX_EMAIL_LENGTH = 254
MAX_DOMAIN_LENGTH = 253
MAX_SUBJECT_LENGTH = 200
MAX_SUMMARY_LENGTH = 2000
# Erlaubte Werte wie im Schema (CHECKs von contacts.language, customers.country, customers.industry);
# test_write_inputs.py vergleicht sie mit db/schema.sql
ALLOWED_LANGUAGES = ("de", "en")
ALLOWED_COUNTRIES = ("de", "at", "ch")
ALLOWED_INDUSTRIES = (
    "automotive",
    "food",
    "packaging",
    "metalworking",
    "plastics",
    "logistics",
    "other",
)
DEFAULT_INDUSTRY = "other"
# Nur diese Typen darf die Schreibrolle anlegen (INSERT-Policy auf activities in db/roles/data_service_rw.sql)
ACTIVITY_TYPES = ("inquiry", "complaint", "note")


def _listing(values: tuple[str, ...]) -> str:
    """Werte für eine Meldung: "de und en", "de, at und ch"."""
    return f"{', '.join(values[:-1])} und {values[-1]}"


_CONTROL_CHARACTERS = "Steuer-, Format- oder Trennzeichen"

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

# Meldungen der Schreib-Werkzeuge (F08): je Eingabe eine feste Meldung mit Feldname und Regel, nie mit dem Wert
COMPANY_NAME_INVALID = (
    f"Der Firmenname ist ungültig: Erwartet werden 1 bis {MAX_COMPANY_NAME_LENGTH} Zeichen, "
    f"ohne {_CONTROL_CHARACTERS}."
)
FIRST_NAME_INVALID = (
    f"Der Vorname ist ungültig: Erwartet werden 1 bis {MAX_PERSON_NAME_LENGTH} Zeichen, "
    f"ohne {_CONTROL_CHARACTERS}."
)
LAST_NAME_INVALID = (
    f"Der Nachname ist ungültig: Erwartet werden 1 bis {MAX_PERSON_NAME_LENGTH} Zeichen, "
    f"ohne {_CONTROL_CHARACTERS}."
)
JOB_TITLE_INVALID = (
    f"Die Position ist ungültig: Erwartet werden höchstens {MAX_JOB_TITLE_LENGTH} Zeichen, "
    f"ohne {_CONTROL_CHARACTERS}; sie darf fehlen."
)
EMAIL_INVALID = (
    f"Die E-Mail-Adresse ist ungültig: Erwartet wird eine Adresse mit höchstens {MAX_EMAIL_LENGTH} "
    f"Zeichen, genau einem @ und einer Domain auf .example, ohne Leerraum, {_CONTROL_CHARACTERS}. "
    "Der Teil vor dem @ besteht nur aus ASCII-Zeichen."
)
DOMAIN_INVALID = (
    f"Die Domain ist ungültig: Erwartet wird eine Domain auf .example mit höchstens "
    f"{MAX_DOMAIN_LENGTH} Zeichen aus ASCII-Buchstaben, Ziffern, Bindestrich und Punkt."
)
LANGUAGE_INVALID = f"Die Sprache ist ungültig: Erlaubt sind {_listing(ALLOWED_LANGUAGES)}."
COUNTRY_INVALID = f"Das Land ist ungültig: Erlaubt sind {_listing(ALLOWED_COUNTRIES)}."
INDUSTRY_INVALID = (
    f"Die Branche ist ungültig: Erlaubt sind {_listing(ALLOWED_INDUSTRIES)}; "
    f"ohne Angabe gilt {DEFAULT_INDUSTRY}."
)
ACTIVITY_TYPE_INVALID = f"Der Aktivitätstyp ist ungültig: Erlaubt sind {_listing(ACTIVITY_TYPES)}."
SUBJECT_INVALID = (
    f"Der Betreff ist ungültig: Erwartet werden 1 bis {MAX_SUBJECT_LENGTH} Zeichen; Zeilenumbruch, "
    f"Wagenrücklauf und Tab sind erlaubt, andere {_CONTROL_CHARACTERS} nicht."
)
SUMMARY_INVALID = (
    f"Die Zusammenfassung ist ungültig: Erwartet werden 1 bis {MAX_SUMMARY_LENGTH} Zeichen; "
    f"Zeilenumbruch, Wagenrücklauf und Tab sind erlaubt, andere {_CONTROL_CHARACTERS} nicht."
)
# log_activity (F08): feste Meldungen ohne Domain und ohne Adresse. CONTACT_NOT_FOUND steht für "Kontakt unbekannt" und
# für "Kontakt gehört zu einem anderen Kunden": Die Meldung verrät nicht, ob die Adresse bei einem anderen Kunden
# existiert.
CUSTOMER_NOT_FOUND = "Der Kunde ist nicht bekannt: Zu dieser Domain gibt es keinen Kunden."
CONTACT_NOT_FOUND = "Der Kontakt ist für diesen Kunden nicht bekannt."
# create_lead (F08 Etappe 4c): feste Meldung für jeden Konflikt mit bestehenden Daten (Firmenname oder Adresse gehören
# einem anderen Kunden als die Domain). Sie nennt weder Domain noch Firmenname noch Adresse.
LEAD_CONFLICT = (
    "Der Lead steht in Konflikt mit bestehenden Daten und wurde nicht angelegt. "
    "Ein Mitarbeiter muss ihn prüfen."
)

_ARTICLE_NUMBER = re.compile(r"[A-Z]{2}-[0-9]{4}(-[A-Z0-9]{1,4})?")
# Die CHECKs von customers.domain und contacts.email in db/schema.sql (ohne ^ und $, mit fullmatch benutzt).
# Nicht zu verwechseln mit crm._DOMAIN: Das ist die lockere Regel der Suche und bleibt unverändert.
_SCHEMA_DOMAIN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.example")
# In Betreff und Zusammenfassung erlaubt, obwohl es Steuerzeichen sind
_TEXT_LAYOUT = frozenset("\n\t\r")


def _is_rejected_character(character: str) -> bool:
    category = unicodedata.category(character)
    return category.startswith("C") or category in ("Zl", "Zp")


def _prepare(value: Any, message: str, max_length: int, min_length: int = 1) -> str:
    """Typ (nur str), Ränder entfernen, Länge in Zeichen: der bereinigte Text oder ToolError(message)."""
    if not isinstance(value, str):
        raise ToolError(message)
    text = value.strip()
    if not min_length <= len(text) <= max_length:
        raise ToolError(message)
    return text


def _check_text(value: Any, message: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    text = _prepare(value, message, max_length)
    if any(_is_rejected_character(character) for character in text):
        raise ToolError(message)
    return text


def _check_free_text(value: Any, message: str, max_length: int) -> str:
    """Mehrzeiliger Text: wie _check_text, aber Zeilenumbruch, Wagenrücklauf und Tab bleiben erlaubt."""
    text = _prepare(value, message, max_length)
    if any(c not in _TEXT_LAYOUT and _is_rejected_character(c) for c in text):
        raise ToolError(message)
    return text


def _check_choice(value: Any, allowed: tuple[str, ...], message: str) -> str:
    """Exakt einer der erlaubten Werte, ohne strip und ohne lower; sonst ToolError(message)."""
    if not isinstance(value, str) or value not in allowed:
        raise ToolError(message)
    return value


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


# Schreib-Werkzeuge (F08, ADR 0005): create_lead und log_activity


def check_company_name(value: Any) -> str:
    """Firmenname, 1 bis MAX_COMPANY_NAME_LENGTH Zeichen: der bereinigte Text oder ToolError(COMPANY_NAME_INVALID)."""
    return _check_text(value, COMPANY_NAME_INVALID, MAX_COMPANY_NAME_LENGTH)


def check_first_name(value: Any) -> str:
    """Vorname, 1 bis MAX_PERSON_NAME_LENGTH Zeichen: der bereinigte Text oder ToolError(FIRST_NAME_INVALID)."""
    return _check_text(value, FIRST_NAME_INVALID, MAX_PERSON_NAME_LENGTH)


def check_last_name(value: Any) -> str:
    """Nachname, 1 bis MAX_PERSON_NAME_LENGTH Zeichen: der bereinigte Text oder ToolError(LAST_NAME_INVALID)."""
    return _check_text(value, LAST_NAME_INVALID, MAX_PERSON_NAME_LENGTH)


def check_job_title(value: Any) -> str | None:
    """Position (optional): None, wenn sie fehlt, leer oder nur Leerraum ist; sonst der bereinigte Text."""
    if value is None:
        return None
    text = _prepare(value, JOB_TITLE_INVALID, MAX_JOB_TITLE_LENGTH, min_length=0)
    if not text:
        return None
    if any(_is_rejected_character(character) for character in text):
        raise ToolError(JOB_TITLE_INVALID)
    return text


def check_email(value: Any) -> str:
    """E-Mail-Adresse: die bereinigte Adresse in Kleinbuchstaben oder ToolError(EMAIL_INVALID).

    Das Schema verlangt email = lower(email), deshalb wird die ganze Adresse klein zurückgegeben. Der Teil vor dem @
    und die Domain müssen reines ASCII sein: Dann gibt lower() in Python und in PostgreSQL dasselbe Ergebnis.
    """
    text = _prepare(value, EMAIL_INVALID, MAX_EMAIL_LENGTH)
    if any(character.isspace() or _is_rejected_character(character) for character in text):
        raise ToolError(EMAIL_INVALID)
    local, separator, domain = text.partition("@")
    # Genau ein @ (ein zweites steht dann in domain), Lokalteil nicht leer; Lokalteil und Domain nur als ASCII, weil
    # lower() sonst Nicht-ASCII-Zeichen in ASCII-Buchstaben verwandeln würde (zum Beispiel das Kelvin-Zeichen in k)
    # und das lower() von PostgreSQL von der Locale der Datenbank abhängt
    if not separator or not local or "@" in domain or not local.isascii() or not domain.isascii():
        raise ToolError(EMAIL_INVALID)
    domain = domain.lower()
    if _SCHEMA_DOMAIN.fullmatch(domain) is None:
        raise ToolError(EMAIL_INVALID)
    return f"{local.lower()}@{domain}"


def email_domain(email: str) -> str:
    """Die Domain einer mit check_email geprüften Adresse (der Teil nach dem letzten @)."""
    return email.rpartition("@")[2]


def check_domain(value: Any) -> str:
    """Kundendomain: die bereinigte Domain in Kleinbuchstaben oder ToolError(DOMAIN_INVALID).

    Entspricht den CHECKs von customers.domain und contacts.email (nur [a-z0-9-]-Labels, endet auf .example).
    """
    text = _prepare(value, DOMAIN_INVALID, MAX_DOMAIN_LENGTH)
    # Erst ASCII prüfen, dann lower(): lower() macht aus Nicht-ASCII-Zeichen ASCII-Buchstaben
    if not text.isascii():
        raise ToolError(DOMAIN_INVALID)
    text = text.lower()
    if _SCHEMA_DOMAIN.fullmatch(text) is None:
        raise ToolError(DOMAIN_INVALID)
    return text


def check_language(value: Any) -> str:
    """Sprache des Kontakts: exakt ein Wert aus ALLOWED_LANGUAGES, sonst ToolError(LANGUAGE_INVALID)."""
    return _check_choice(value, ALLOWED_LANGUAGES, LANGUAGE_INVALID)


def check_country(value: Any) -> str:
    """Land (Pflicht): exakt ein Wert aus ALLOWED_COUNTRIES, sonst ToolError(COUNTRY_INVALID)."""
    return _check_choice(value, ALLOWED_COUNTRIES, COUNTRY_INVALID)


def check_industry(value: Any) -> str:
    """Branche (optional): DEFAULT_INDUSTRY, wenn sie fehlt (None); sonst exakt ein Wert aus ALLOWED_INDUSTRIES."""
    if value is None:
        return DEFAULT_INDUSTRY
    return _check_choice(value, ALLOWED_INDUSTRIES, INDUSTRY_INVALID)


def check_activity_type(value: Any) -> str:
    """Aktivitätstyp: exakt inquiry, complaint oder note, sonst ToolError(ACTIVITY_TYPE_INVALID)."""
    return _check_choice(value, ACTIVITY_TYPES, ACTIVITY_TYPE_INVALID)


def check_subject(value: Any) -> str:
    """Betreff, 1 bis MAX_SUBJECT_LENGTH Zeichen: der bereinigte Text oder ToolError(SUBJECT_INVALID)."""
    return _check_free_text(value, SUBJECT_INVALID, MAX_SUBJECT_LENGTH)


def check_summary(value: Any) -> str:
    """Zusammenfassung, 1 bis MAX_SUMMARY_LENGTH Zeichen: der bereinigte Text oder ToolError(SUMMARY_INVALID)."""
    return _check_free_text(value, SUMMARY_INVALID, MAX_SUMMARY_LENGTH)
