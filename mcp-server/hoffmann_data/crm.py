"""Lesende CRM-Abfragen des Datenservice (F07): find_customer.

Nur Lesen, Werte nur als benannte Parameter. Die Abfragetexte sind feste Konstanten; Eingaben werden nie in
sie eingesetzt (Test: test_package_sql_rules.py). Die Eingabe prüft limits.py, bevor die Datenbank berührt wird.

Abgleich (immer exakt, ohne Beachtung der Schreibung; Subdomains und Teilnamen treffen nicht):
- Eingabe mit "@": zuerst der Kontakt über die Adresse (matched_by "email"), sonst der Kunde über die Domain
  der Adresse (matched_by "domain").
- Eingabe ohne "@": zuerst der Firmenname (matched_by "company"), sonst, wenn sie wie eine Domain aussieht,
  die Domain (matched_by "domain").
Mehrdeutigkeit ist ein fester Fehler, nie Raten. Heute machen die UNIQUE-Constraints auf domain, email und
lower(company_name) sie unmöglich; die Abfragen holen trotzdem höchstens zwei Zeilen und pick_unique entscheidet.
Dublettenlogik und Anlegen von Leads gehören nicht hierher (F08).
"""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict

from mcp.server.mcpserver.exceptions import ToolError

from hoffmann_data.db import ConnectionSource, fetch_all, read_connection
from hoffmann_data.limits import check_customer_query

AMBIGUOUS_MESSAGE = "Mehrdeutiger Treffer."
MAX_CONTACTS = 20
MAX_ACTIVITIES = 10

MatchedBy = Literal["company", "email", "domain"]

# Eine Domain im Sinn dieses Abgleichs: ASCII-Labels aus Buchstaben, Ziffern und Bindestrich, mindestens ein
# Punkt. Bewusst nicht an die Endung .example der Testdaten gebunden.
_DOMAIN = re.compile(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


class ContactInfo(TypedDict):
    first_name: str
    last_name: str
    email: str
    job_title: str | None
    language: str


class ActivityInfo(TypedDict):
    type: str
    # ISO-Text in UTC, z. B. "2026-03-01T10:00:00+00:00"
    occurred_at: str
    subject: str
    summary: str
    # Netto-Betrag als Text mit zwei Nachkommastellen oder None (nur bei Angebot und Auftrag)
    amount_eur: str | None
    created_by: str
    # Artikelnummer des Produkts, auf das sich der Eintrag bezieht, sonst None
    article_number: str | None


class CustomerInfo(TypedDict):
    company_name: str
    domain: str
    industry: str
    country: str
    status: str
    contacts: list[ContactInfo]
    recent_activities: list[ActivityInfo]


class CustomerResult(TypedDict):
    customer: CustomerInfo | None
    matched_by: MatchedBy | None


# Die drei Wege zum Kunden. cu.id dient nur zum Weiterverbinden und wird nie ausgegeben.
CUSTOMER_BY_EMAIL_SQL = """
SELECT cu.id, cu.company_name, cu.domain, cu.industry, cu.country, cu.status
FROM contacts ct
JOIN customers cu ON cu.id = ct.customer_id
WHERE ct.email = lower(%(value)s::text)
LIMIT 2
"""

CUSTOMER_BY_DOMAIN_SQL = """
SELECT cu.id, cu.company_name, cu.domain, cu.industry, cu.country, cu.status
FROM customers cu
WHERE cu.domain = lower(%(value)s::text)
LIMIT 2
"""

CUSTOMER_BY_COMPANY_SQL = """
SELECT cu.id, cu.company_name, cu.domain, cu.industry, cu.country, cu.status
FROM customers cu
WHERE lower(cu.company_name) = lower(%(value)s::text)
LIMIT 2
"""

# Der über die Adresse gefundene Kontakt steht zuerst (er darf bei mehr als 20 Kontakten nicht fehlen), dann
# nach Name; die Adresse ist eindeutig und beendet die Sortierung. email ist NULL, wenn nicht über die Adresse gefunden.
CONTACTS_SQL = """
SELECT first_name, last_name, email, job_title, language
FROM contacts
WHERE customer_id = %(customer_id)s
ORDER BY
    COALESCE(email = lower(%(email)s::text), false) DESC,
    last_name,
    first_name,
    email
LIMIT %(n)s
"""

# Neueste zuerst; bei gleicher Zeit der später angelegte Eintrag zuerst (id dient nur zum Sortieren).
ACTIVITIES_SQL = """
SELECT a.type, a.occurred_at, a.subject, a.summary, a.amount_eur, a.created_by, p.article_number
FROM activities a
LEFT JOIN products p ON p.id = a.product_id
WHERE a.customer_id = %(customer_id)s
ORDER BY a.occurred_at DESC, a.id DESC
LIMIT %(n)s
"""


def pick_unique(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Keine Zeile: None. Eine Zeile: diese. Mehr als eine: ToolError ohne Wert, es wird nie geraten."""
    if not rows:
        return None
    if len(rows) > 1:
        raise ToolError(AMBIGUOUS_MESSAGE)
    return rows[0]


def _looks_like_domain(text: str) -> bool:
    return _DOMAIN.fullmatch(text) is not None


def _find_by_domain(connection: Any, domain: str) -> tuple[dict[str, Any], MatchedBy] | None:
    row = pick_unique(fetch_all(connection, CUSTOMER_BY_DOMAIN_SQL, {"value": domain}))
    return None if row is None else (row, "domain")


def _lookup(connection: Any, text: str) -> tuple[dict[str, Any], MatchedBy] | None:
    """Findet den Kunden zur Eingabe und sagt, auf welchem Weg. None: unbekannt."""
    if "@" in text:
        local, _, domain = text.rpartition("@")
        row = pick_unique(fetch_all(connection, CUSTOMER_BY_EMAIL_SQL, {"value": text}))
        if row is not None:
            return row, "email"
        # Ohne Lokalteil ist es keine Adresse; nichts wird geraten
        if local and _looks_like_domain(domain):
            return _find_by_domain(connection, domain)
        return None
    row = pick_unique(fetch_all(connection, CUSTOMER_BY_COMPANY_SQL, {"value": text}))
    if row is not None:
        return row, "company"
    if _looks_like_domain(text):
        return _find_by_domain(connection, text)
    return None


def _format_amount(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _format_time(value: datetime) -> str:
    # In UTC ausgeben: Das Ergebnis hängt so nicht von der Zeitzone der Sitzung ab
    return value.astimezone(UTC).isoformat()


def find_customer(database: ConnectionSource | None, query: Any) -> CustomerResult:
    """Kunde über Firma, E-Mail-Adresse oder Domain, mit Ansprechpersonen und Historie.

    Ungültige Eingabe: ToolError mit fester Meldung, die Datenbank wird nicht berührt. Unbekannter Kunde:
    {"customer": None, "matched_by": None}, kein Fehler. Auch ein inaktiver Kunde wird geliefert.
    """
    text = check_customer_query(query)
    with read_connection(database) as connection:
        found = _lookup(connection, text)
        if found is None:
            return {"customer": None, "matched_by": None}
        row, matched_by = found
        contact_rows = fetch_all(
            connection,
            CONTACTS_SQL,
            {
                "customer_id": row["id"],
                "email": text if matched_by == "email" else None,
                "n": MAX_CONTACTS,
            },
        )
        activity_rows = fetch_all(
            connection, ACTIVITIES_SQL, {"customer_id": row["id"], "n": MAX_ACTIVITIES}
        )
    # Feld für Feld: Es gehen nie mehr Schlüssel heraus als dokumentiert (keine interne id)
    contacts: list[ContactInfo] = [
        {
            "first_name": contact["first_name"],
            "last_name": contact["last_name"],
            "email": contact["email"],
            "job_title": contact["job_title"],
            "language": contact["language"],
        }
        for contact in contact_rows
    ]
    activities: list[ActivityInfo] = [
        {
            "type": activity["type"],
            "occurred_at": _format_time(activity["occurred_at"]),
            "subject": activity["subject"],
            "summary": activity["summary"],
            "amount_eur": _format_amount(activity["amount_eur"]),
            "created_by": activity["created_by"],
            "article_number": activity["article_number"],
        }
        for activity in activity_rows
    ]
    customer: CustomerInfo = {
        "company_name": row["company_name"],
        "domain": row["domain"],
        "industry": row["industry"],
        "country": row["country"],
        "status": row["status"],
        "contacts": contacts,
        "recent_activities": activities,
    }
    return {"customer": customer, "matched_by": matched_by}
