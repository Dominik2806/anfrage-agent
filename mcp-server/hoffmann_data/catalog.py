"""Lesende Katalog-Abfragen des Datenservice (F07): search_products und get_product.

Nur Lesen, Werte nur als benannte Parameter. Die Abfragetexte sind feste Konstanten; Eingaben werden nie in
sie eingesetzt (Test: test_package_sql_rules.py). Eingaben prüft limits.py, bevor die Datenbank berührt wird.
"""

from decimal import Decimal
from typing import Any, TypedDict

from hoffmann_data.db import ConnectionSource, fetch_all, read_connection
from hoffmann_data.limits import DEFAULT_LIMIT, check_article_number, check_limit, check_query


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
# oder Volltext "german" über Name und Beschreibung. Der Suchtext geht unverändert an websearch_to_tsquery:
# Wörter ohne Operator sind UND-verknüpft, die Operatoren der Funktion (or, Anführungszeichen, -) werden nicht ausgeschlossen.
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


class LinkedProduct(TypedDict):
    """Ein Verweis aus product_fits, nur über natürliche Schlüssel (keine interne id)."""

    article_number: str
    name: str
    note: str | None


class ProductDetail(TypedDict):
    article_number: str
    name: str
    category: str
    description: str
    technical_data: dict[str, Any]
    # Netto-Listenpreis als Text mit zwei Nachkommastellen, z. B. "890.00", je price_unit
    list_price: str
    price_unit: str
    lead_time_days: int
    is_active: bool
    # Anlagen, zu denen dieses Ersatzteil passt
    fits_assemblies: list[LinkedProduct]
    # Ersatzteile, die zu dieser Anlage passen
    compatible_parts: list[LinkedProduct]


class ProductResult(TypedDict):
    product: ProductDetail | None


# Eine Abfrage, ein Snapshot: das Produkt und beide Richtungen von product_fits. Die Verweise stehen als
# JSON-Liste nach Artikelnummer sortiert; ohne Zeile in product_fits sind es leere Listen. Interne ids werden
# nur zum Verbinden benutzt und nie ausgewählt. Varianten erben keine Zuordnung (nur Zeilen dieses Artikels).
GET_PRODUCT_SQL = """
SELECT
    p.article_number, p.name, p.category, p.description, p.technical_data,
    p.list_price, p.price_unit, p.lead_time_days, p.is_active,
    COALESCE((
        SELECT jsonb_agg(
            jsonb_build_object('article_number', a.article_number, 'name', a.name, 'note', f.note)
            ORDER BY a.article_number
        )
        FROM product_fits f
        JOIN products a ON a.id = f.fits_product_id
        WHERE f.product_id = p.id
    ), '[]'::jsonb) AS fits_assemblies,
    COALESCE((
        SELECT jsonb_agg(
            jsonb_build_object('article_number', c.article_number, 'name', c.name, 'note', f.note)
            ORDER BY c.article_number
        )
        FROM product_fits f
        JOIN products c ON c.id = f.product_id
        WHERE f.fits_product_id = p.id
    ), '[]'::jsonb) AS compatible_parts
FROM products p
WHERE p.article_number = %(art)s::text
"""


def _format_price(value: Decimal) -> str:
    """Preis als Text mit genau zwei Nachkommastellen, unabhängig von der Skala der Spalte."""
    return f"{value:.2f}"


def _links(entries: list[dict[str, Any]]) -> list[LinkedProduct]:
    # Feld für Feld: Es gehen nie mehr Schlüssel heraus als dokumentiert
    return [
        {"article_number": entry["article_number"], "name": entry["name"], "note": entry["note"]}
        for entry in entries
    ]


def get_product(database: ConnectionSource | None, article_number: Any) -> ProductResult:
    """Details, Listenpreis und Lieferzeit zu einer Artikelnummer.

    Ungültige Nummer: ToolError mit fester Meldung, die Datenbank wird nicht berührt. Unbekannte Nummer:
    {"product": None}, kein Fehler. Ein inaktiver Artikel wird geliefert (is_active=False).
    """
    number = check_article_number(article_number)
    with read_connection(database) as connection:
        rows = fetch_all(connection, GET_PRODUCT_SQL, {"art": number})
    if not rows:
        return {"product": None}
    row = rows[0]
    return {
        "product": {
            "article_number": row["article_number"],
            "name": row["name"],
            "category": row["category"],
            "description": row["description"],
            "technical_data": row["technical_data"],
            "list_price": _format_price(row["list_price"]),
            "price_unit": row["price_unit"],
            "lead_time_days": row["lead_time_days"],
            "is_active": row["is_active"],
            "fits_assemblies": _links(row["fits_assemblies"]),
            "compatible_parts": _links(row["compatible_parts"]),
        }
    }
