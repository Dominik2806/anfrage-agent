"""Lesende Katalog-Abfragen des Datenservice (F07): search_products.

Nur Lesen, Werte nur als benannte Parameter. Der Abfragetext ist eine feste Konstante; Eingaben werden nie in
ihn eingesetzt (Test: test_package_sql_rules.py). Eingaben prüft limits.py, bevor die Datenbank berührt wird.
"""

from typing import Any, TypedDict

from hoffmann_data.db import ConnectionSource, fetch_all, read_connection
from hoffmann_data.limits import DEFAULT_LIMIT, check_limit, check_query


class ProductHit(TypedDict):
    """Ein Treffer der Suche. Preise und Lieferzeiten gibt nur get_product heraus."""

    article_number: str
    name: str
    category: str
    is_active: bool


class SearchResult(TypedDict):
    items: list[ProductHit]
    count: int


# Treffer sind: Artikelnummer exakt oder als Präfix (starts_with, nicht LIKE: % und _ sind keine Platzhalter)
# oder Volltext "german" über Name und Beschreibung (websearch_to_tsquery: nur UND, kein ODER-Fallback).
# Reihenfolge: exakter Treffer zuerst (auch wenn der Artikel inaktiv ist), dann aktive vor inaktiven, darin
# Präfixtreffer, dann Volltext nach Rang, zuletzt die Artikelnummer.
# art ist die Eingabe in Großbuchstaben oder NULL (nicht ASCII); alle Eingaben stehen als ::text, sonst kann
# der Server den Typ eines Parameters nicht bestimmen.
SEARCH_SQL = """
SELECT article_number, name, category, is_active
FROM products
WHERE article_number = %(art)s::text
   OR starts_with(article_number, %(art)s::text)
   OR to_tsvector('german', name || ' ' || description)
      @@ websearch_to_tsquery('german', %(q)s::text)
ORDER BY
    COALESCE(article_number = %(art)s::text, false) DESC,
    is_active DESC,
    COALESCE(starts_with(article_number, %(art)s::text), false) DESC,
    ts_rank(
        to_tsvector('german', name || ' ' || description),
        websearch_to_tsquery('german', %(q)s::text)
    ) DESC,
    article_number
LIMIT %(n)s
"""


def search_products(
    database: ConnectionSource | None, query: Any, limit: Any = DEFAULT_LIMIT
) -> SearchResult:
    """Freitextsuche im Katalog. Ungültige Eingabe: ToolError mit fester Meldung. Kein Treffer: leere Liste."""
    text = check_query(query)
    count = check_limit(limit)
    # upper() nur für ASCII: Nicht-ASCII-Zeichen (z. B. langes s) würden sonst zu Buchstaben einer Artikelnummer
    article_number = text.upper() if text.isascii() else None
    with read_connection(database) as connection:
        rows = fetch_all(connection, SEARCH_SQL, {"art": article_number, "q": text, "n": count})
    # Feld für Feld: Es gehen nie mehr Spalten heraus als dokumentiert (keine interne id, kein Preis)
    items: list[ProductHit] = [
        {
            "article_number": row["article_number"],
            "name": row["name"],
            "category": row["category"],
            "is_active": row["is_active"],
        }
        for row in rows
    ]
    return {"items": items, "count": len(items)}
