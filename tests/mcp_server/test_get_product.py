"""Tests des Werkzeugs get_product (F07, Auftrag 6.4): Details, Listenpreis und Lieferzeit zu einer Artikelnummer.

Die Tests laufen durch die ganze App gegen PostgreSQL unter der lesenden Rolle (Fixture tool_call) und
brauchen TEST_DATABASE_URL. Der Katalog kommt aus data/stammdaten (Fixture catalog).

Festgelegt (docs/plans/F07-stand.md, Abschnitt c und d): Ein unbekannter Artikel ist `{"product": null}`,
kein Fehler. Preise als Text mit zwei Nachkommastellen. Keine internen IDs; Verweise über Artikelnummern.
Ersatzteile (`fits_assemblies`: passt zu Anlage) und Zubehör einer Anlage (`compatible_parts`) stammen aus
product_fits; Varianten erben die Zuordnung nicht.
"""

import json
import logging
import re
from decimal import Decimal
from typing import Any

import pytest
from mcp_testkit import (
    PRODUCTS_JSON,
    SECRET_MARKER,
    assert_no_internal_ids,
    error_text,
    structured,
)

PRODUCT_KEYS = {
    "article_number",
    "name",
    "category",
    "description",
    "technical_data",
    "list_price",
    "price_unit",
    "lead_time_days",
    "is_active",
    "fits_assemblies",
    "compatible_parts",
}
LINK_KEYS = {"article_number", "name", "note"}
PRICE_PATTERN = re.compile(r"\d+\.\d{2}")
CATALOG = {p["article_number"]: p for p in json.loads(PRODUCTS_JSON.read_text(encoding="utf-8"))}


def _get(tool_call: Any, article_number: Any) -> dict[str, Any]:
    return tool_call("get_product", {"article_number": article_number})


def _product(tool_call: Any, article_number: str) -> dict[str, Any]:
    product = structured(_get(tool_call, article_number))["product"]
    assert product is not None, f"{article_number} nicht gefunden"
    return product


def _links(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["article_number"]: entry for entry in entries}


# Treffer


def test_result_has_exactly_the_key_product(catalog: Any, tool_call: Any) -> None:
    assert set(structured(_get(tool_call, "FB-1001"))) == {"product"}


def test_product_has_exactly_the_documented_fields(catalog: Any, tool_call: Any) -> None:
    assert set(_product(tool_call, "FB-1001")) == PRODUCT_KEYS


def test_known_product_has_the_values_of_the_database(catalog: Any, tool_call: Any) -> None:
    product = _product(tool_call, "FB-1001")
    assert product["article_number"] == "FB-1001"
    assert product["name"] == "Gurtförderer Standard"
    assert product["category"] == "conveyor"
    assert product["list_price"] == "890.00"
    assert product["price_unit"] == "meter"
    assert product["lead_time_days"] == 30
    assert product["is_active"] is True
    assert "Gurtband" in product["description"]
    assert product["technical_data"]["belt_width_mm"] == 500


def test_every_catalog_product_matches_its_source_data(catalog: Any, tool_call: Any) -> None:
    """Alle 40 Artikel: Preis, Einheit, Lieferzeit, technische Daten und Status stimmen mit den Stammdaten."""
    assert len(CATALOG) >= 40
    for number, source in CATALOG.items():
        product = _product(tool_call, number)
        assert Decimal(product["list_price"]) == Decimal(str(source["list_price"])), number
        assert PRICE_PATTERN.fullmatch(product["list_price"]), number
        assert product["price_unit"] == source["price_unit"], number
        assert product["lead_time_days"] == source["lead_time_days"], number
        assert product["technical_data"] == source["technical_data"], number
        assert product["is_active"] == source["is_active"], number
        assert product["name"] == source["name"], number
        assert product["description"] == source["description"], number


def test_product_has_no_internal_ids(catalog: Any, tool_call: Any) -> None:
    assert_no_internal_ids(structured(_get(tool_call, "ET-3001")))
    assert_no_internal_ids(structured(_get(tool_call, "FB-1001")))


@pytest.mark.parametrize(
    ("list_price", "expected"),
    [
        (12.5, "12.50"),
        (0, "0.00"),
        (890, "890.00"),
        (1234.56, "1234.56"),
        (99999999.99, "99999999.99"),
    ],
)
def test_prices_are_text_with_two_decimals(
    make: Any, tool_call: Any, list_price: float, expected: str
) -> None:
    make.product(article_number="AB-0100", list_price=list_price)
    assert _product(tool_call, "AB-0100")["list_price"] == expected


def test_lead_time_is_a_whole_number_of_days(make: Any, tool_call: Any) -> None:
    make.product(article_number="AB-0101", lead_time_days=0)
    lead_time = _product(tool_call, "AB-0101")["lead_time_days"]
    assert lead_time == 0 and isinstance(lead_time, int) and not isinstance(lead_time, bool)


def test_product_without_technical_data_has_an_empty_object(make: Any, tool_call: Any) -> None:
    make.product(article_number="AB-0102")
    assert _product(tool_call, "AB-0102")["technical_data"] == {}


# Zuordnung: Ersatzteil passt zu Anlage


def test_spare_part_lists_the_assemblies_it_fits(catalog: Any, tool_call: Any) -> None:
    product = _product(tool_call, "ET-3001")
    assert set(_links(product["fits_assemblies"])) == {"FB-1001", "FB-1002", "FB-1007"}
    assert product["compatible_parts"] == []
    for entry in product["fits_assemblies"]:
        assert set(entry) == LINK_KEYS
        assert entry["name"] == CATALOG[entry["article_number"]]["name"]
        assert entry["note"] is None


def test_assembly_lists_its_compatible_parts(catalog: Any, tool_call: Any) -> None:
    product = _product(tool_call, "FB-1001")
    parts = _links(product["compatible_parts"])
    assert set(parts) == {"ET-3001", "ET-3004", "ET-3006", "ET-3007", "ET-3008", "ET-3016"}
    assert product["fits_assemblies"] == []
    assert parts["ET-3007"]["note"] == "Breite nach Variante wählen"
    assert parts["ET-3007"]["name"] == "Fördergurt"
    assert all(set(entry) == LINK_KEYS for entry in parts.values())


def test_variant_does_not_inherit_the_parts_of_its_base_article(
    catalog: Any, tool_call: Any
) -> None:
    """FB-1001-B8 ist eine Variante von FB-1001, hat aber keine eigene Zuordnung (absichtliche Lücke, F23)."""
    assert _product(tool_call, "FB-1001-B8")["compatible_parts"] == []


def test_spare_part_without_assignment_has_empty_lists(catalog: Any, tool_call: Any) -> None:
    """ET-3009 (Lagersatz) hat keine Zeile in product_fits: Der Agent darf keine Zuordnung erfinden."""
    product = _product(tool_call, "ET-3009")
    assert product["fits_assemblies"] == [] and product["compatible_parts"] == []


def test_note_of_an_assignment_is_returned(catalog: Any, tool_call: Any) -> None:
    fits = _links(_product(tool_call, "ET-3010")["fits_assemblies"])
    assert fits["FB-1007"]["note"] == "Ersatz für die ausgelaufene Steuerung"
    assert fits["FB-1005"]["note"] is None


# Inaktiver Artikel


def test_inactive_product_is_returned_and_flagged(catalog: Any, tool_call: Any) -> None:
    """ET-3014 ist ausgelaufen: Er wird geliefert (nicht null), mit is_active = false und dem Nachfolger."""
    product = _product(tool_call, "ET-3014")
    assert product["is_active"] is False
    assert product["technical_data"]["successor"] == "ET-3010"
    assert "ET-3010" in product["description"]
    assert _links(product["fits_assemblies"])["FB-1007"]["note"] == "Ausgelaufen"


# Normalisierung der Artikelnummer


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("fb-1001", "FB-1001"),
        ("  FB-1001  ", "FB-1001"),
        (" fb-1001-b8 ", "FB-1001-B8"),
        ("Et-3014", "ET-3014"),
    ],
)
def test_article_number_is_stripped_and_upper_cased(
    catalog: Any, tool_call: Any, given: str, expected: str
) -> None:
    assert _product(tool_call, given)["article_number"] == expected


# Unbekannte Artikelnummer: null, kein Fehler


@pytest.mark.parametrize("number", ["XX-9999", "FB-9999", "FB-1001-ZZ", "ET-3099"])
def test_unknown_article_number_gives_null_not_an_error(
    catalog: Any, tool_call: Any, number: str
) -> None:
    result = _get(tool_call, number)
    assert result.get("isError") is not True
    assert structured(result) == {"product": None}


def test_valid_number_in_an_empty_catalog_gives_null(tool_call: Any) -> None:
    assert structured(_get(tool_call, "FB-1001")) == {"product": None}


# Ungültige Eingabe: feste Meldung, kein Echo


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "FB1001",
        "FB-100",
        "FB-1001-ABCDE",
        "FB-1001\nFB-1002",
        "FB-1001\x00",
        "FB-1001" + "0" * 200,
        "FB-1001'; DROP TABLE products; --",
        "FB-1001' OR '1'='1",
        "FB-%",
        "FB-____",
        SECRET_MARKER,
    ],
    ids=repr,
)
def test_invalid_article_number_is_rejected_with_the_fixed_message(
    catalog: Any, tool_call: Any, conn: Any, value: str
) -> None:
    from hoffmann_data.db import DATABASE_ERROR
    from hoffmann_data.limits import ARTICLE_NUMBER_INVALID

    before = conn.execute("SELECT count(*) FROM products").fetchone()
    result = _get(tool_call, value)
    text = error_text(result)
    assert ARTICLE_NUMBER_INVALID in text
    assert DATABASE_ERROR not in text
    assert SECRET_MARKER not in text
    assert not result.get("structuredContent")
    assert conn.execute("SELECT count(*) FROM products").fetchone() == before


@pytest.mark.parametrize(
    "value",
    [1001, True, 7.5, None, ["FB-1001"], [SECRET_MARKER], {"a": SECRET_MARKER}],
    ids=["zahl", "true", "kommazahl", "null", "liste", "liste-marker", "objekt-marker"],
)
def test_article_number_must_be_text(catalog: Any, tool_call: Any, value: Any) -> None:
    from hoffmann_data.limits import ARTICLE_NUMBER_INVALID

    result = _get(tool_call, value)
    text = error_text(result)
    assert ARTICLE_NUMBER_INVALID in text
    assert SECRET_MARKER not in text
    assert not result.get("structuredContent")


def test_missing_article_number_is_an_error(catalog: Any, tool_call: Any) -> None:
    assert tool_call("get_product", {}).get("isError") is True


# Kein Geheimnis im Log, nur lesen


def test_input_is_not_echoed_in_response_log_or_output(
    catalog: Any,
    tool_call: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    invalid = _get(tool_call, SECRET_MARKER)
    unknown = _get(tool_call, "XX-9999")
    assert error_text(invalid)
    assert structured(unknown) == {"product": None}
    captured = capsys.readouterr()
    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for text in (str(invalid), str(unknown), logged, captured.out, captured.err):
        assert SECRET_MARKER not in text
    assert "XX-9999" not in logged, "Eingabewerte gehören nicht ins Log"
