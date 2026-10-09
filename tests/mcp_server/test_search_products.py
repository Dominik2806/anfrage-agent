"""Tests des Werkzeugs search_products (F07, Auftrag 6.4): Freitextsuche im Katalog.

Die Tests laufen durch die ganze App (Token, Transport, SDK) gegen PostgreSQL unter der lesenden Rolle
(Fixture tool_call) und brauchen TEST_DATABASE_URL. Der Katalog kommt aus data/stammdaten/products.json
(Fixture catalog); einzelne Fälle legen eigene Artikel an (Fixture make).

Festgelegt (docs/plans/F07-stand.md, Abschnitt d): Volltext "german" über Name und Beschreibung, nur
UND-Verknüpfung (kein ODER-Fallback), dazu Artikelnummer exakt und als Präfix. Ein Nichttreffer ist eine
leere Liste, kein Fehler. Ungültige Eingabe gibt einen ToolError mit fester Meldung ohne Eingabewert.
"""

import logging
from typing import Any

import pytest
from mcp_testkit import (
    SECRET_MARKER,
    assert_no_internal_ids,
    check_rejected_before_database,
    error_text,
    structured,
)

KNOWN_FIVE_TABLES = ("products", "product_fits", "customers", "contacts", "activities")


def _numbers(result: dict[str, Any]) -> list[str]:
    return [item["article_number"] for item in structured(result)["items"]]


def _search(tool_call: Any, query: str, limit: int | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"query": query}
    if limit is not None:
        arguments["limit"] = limit
    return tool_call("search_products", arguments)


# Treffer und Form der Antwort


def test_search_finds_products_by_name(catalog: Any, tool_call: Any) -> None:
    found = _numbers(_search(tool_call, "Gurtförderer Standard", limit=20))
    assert {"FB-1001", "FB-1001-B8"} <= set(found)


def test_result_has_exactly_items_and_count(catalog: Any, tool_call: Any) -> None:
    content = structured(_search(tool_call, "Gurtband"))
    assert set(content) == {"items", "count"}
    assert isinstance(content["items"], list) and content["items"]
    assert content["count"] == len(content["items"])


def test_each_item_has_exactly_the_four_fields(catalog: Any, tool_call: Any) -> None:
    items = structured(_search(tool_call, "Gurtband", limit=20))["items"]
    assert items
    for item in items:
        assert set(item) == {"article_number", "name", "category", "is_active"}
        assert isinstance(item["is_active"], bool)
        assert item["category"] in {"conveyor", "housing", "spare_part", "service_contract"}


def test_item_values_come_from_the_database(catalog: Any, tool_call: Any) -> None:
    items = structured(_search(tool_call, "FB-1001"))["items"]
    first = items[0]
    assert first == {
        "article_number": "FB-1001",
        "name": "Gurtförderer Standard",
        "category": "conveyor",
        "is_active": True,
    }


def test_result_has_no_internal_ids_and_no_prices(catalog: Any, tool_call: Any) -> None:
    """Preise und Lieferzeiten kommen nur aus get_product; die Suche liefert Treffer mit Artikelnummer."""
    result = _search(tool_call, "Gurtband", limit=20)
    assert_no_internal_ids(structured(result))
    assert {"list_price", "lead_time_days", "description"}.isdisjoint(
        {key for item in structured(result)["items"] for key in item}
    )


def test_search_finds_a_product_created_in_the_test(make: Any, tool_call: Any) -> None:
    """Die Daten kommen aus der Datenbank, nicht aus einer festen Liste."""
    make.product(article_number="AB-7777", name="Prüfstandband Sonderanfertigung")
    assert _numbers(_search(tool_call, "Prüfstandband")) == ["AB-7777"]


# Synonyme: Kunden umschreiben Produkte ("Auch: ..." in der Beschreibung, Auftrag 4.4)


@pytest.mark.parametrize(
    ("query", "article_number"),
    [
        ("Gurtband", "FB-1001"),
        ("Bandförderer", "FB-1001"),
        ("Transportband", "FB-1001"),
        ("Steilförderer", "FB-1005"),
        ("Späneförderer", "FB-1004"),
        ("Eckförderer", "FB-1007"),
        ("Kleinteileband", "FB-1002"),
        ("Kunststoffgliederband", "FB-1006"),
        ("Bandmodule", "ET-3013"),
        ("Notaus", "ET-3012"),
        ("Pilztaster", "ET-3012"),
        ("Steuerbox Kurvenförderer", "ET-3014"),
    ],
)
def test_search_finds_products_by_their_alternative_names(
    catalog: Any, tool_call: Any, query: str, article_number: str
) -> None:
    assert article_number in _numbers(_search(tool_call, query, limit=20))


def test_multi_word_synonym_needs_all_words(catalog: Any, tool_call: Any) -> None:
    found = _numbers(_search(tool_call, "Gurtband schwer", limit=20))
    assert "FB-1003" in found
    assert "FB-1002" not in found


def test_search_by_variant_synonym(catalog: Any, tool_call: Any) -> None:
    assert "FB-1001-B8" in _numbers(_search(tool_call, "Gurtband 800", limit=20))


# Groß- und Kleinschreibung


@pytest.mark.parametrize("variant", ["GURTBAND", "gurtband", "GuRtBaNd"])
def test_search_ignores_case(catalog: Any, tool_call: Any, variant: str) -> None:
    reference = _numbers(_search(tool_call, "Gurtband", limit=20))
    assert reference
    assert _numbers(_search(tool_call, variant, limit=20)) == reference


def test_search_ignores_case_with_umlauts(catalog: Any, tool_call: Any) -> None:
    reference = _numbers(_search(tool_call, "Späneförderer", limit=20))
    assert reference
    assert _numbers(_search(tool_call, "SPÄNEFÖRDERER", limit=20)) == reference


# Kein Treffer: leere Liste, kein Fehler


@pytest.mark.parametrize(
    "query",
    [
        "Hubtisch",
        "Zahnriemenförderer",
        "Sortieranlage",
        "Rollenbahn",
        "Kettenförderer",
        "qwertzuiop",
    ],
)
def test_unknown_product_gives_an_empty_list_not_an_error(
    catalog: Any, tool_call: Any, query: str
) -> None:
    result = _search(tool_call, query)
    assert result.get("isError") is not True
    assert structured(result) == {"items": [], "count": 0}


def test_search_in_an_empty_catalog_gives_an_empty_list(tool_call: Any) -> None:
    assert structured(_search(tool_call, "Gurtband")) == {"items": [], "count": 0}


def test_all_words_must_match_no_or_fallback(catalog: Any, tool_call: Any) -> None:
    """Nur UND: Ein Wort, das es gibt, und ein Wort, das es nicht gibt, ergeben keinen Treffer."""
    assert _numbers(_search(tool_call, "Gurtband", limit=20))
    assert _numbers(_search(tool_call, "Gurtband Hubtisch", limit=20)) == []


# Artikelnummer: exakt vor Präfix


@pytest.mark.parametrize(
    ("query", "first", "second"),
    [
        ("FB-1001", "FB-1001", "FB-1001-B8"),
        ("FB-1003", "FB-1003", "FB-1003-X2"),
        ("FB-1008", "FB-1008", "FB-1008-H1"),
    ],
)
def test_exact_article_number_comes_first_then_its_variants(
    catalog: Any, tool_call: Any, query: str, first: str, second: str
) -> None:
    found = _numbers(_search(tool_call, query))
    assert found[:2] == [first, second]


def test_article_number_prefix_finds_the_whole_series(catalog: Any, tool_call: Any) -> None:
    found = _numbers(_search(tool_call, "FB-10", limit=20))
    conveyors = {
        "FB-1001",
        "FB-1001-B8",
        "FB-1002",
        "FB-1003",
        "FB-1003-X2",
        "FB-1004",
        "FB-1005",
        "FB-1006",
        "FB-1007",
        "FB-1008",
        "FB-1008-H1",
    }
    assert conveyors <= set(found)
    assert all(number.startswith("FB-") for number in found)


def test_exact_article_number_of_a_spare_part(catalog: Any, tool_call: Any) -> None:
    assert _numbers(_search(tool_call, "ET-3001"))[0] == "ET-3001"


@pytest.mark.parametrize(
    ("query", "upper_case"),
    [
        ("fb-1001", "FB-1001"),
        ("Fb-1001", "FB-1001"),
        ("  fb-1001  ", "FB-1001"),
        ("fb-1001-b8", "FB-1001-B8"),
        ("et-3014", "ET-3014"),
        ("fb-10", "FB-10"),
    ],
)
def test_article_number_in_lower_case_gives_the_same_result_as_upper_case(
    catalog: Any, tool_call: Any, query: str, upper_case: str
) -> None:
    """Die Artikelnummer wird wie bei get_product zu Großbuchstaben normalisiert (exakt und als Präfix)."""
    expected = _numbers(_search(tool_call, upper_case, limit=20))
    assert expected, "Vergleichsabfrage ohne Treffer"
    assert expected[0].startswith(upper_case)
    assert _numbers(_search(tool_call, query, limit=20)) == expected


# Inaktive Artikel: gekennzeichnet und hinter den aktiven


def test_inactive_product_is_found_and_flagged(catalog: Any, tool_call: Any) -> None:
    """ET-3014 ist ausgelaufen (is_active = false). Er bleibt auffindbar, damit der Agent den Nachfolger nennen kann."""
    items = structured(_search(tool_call, "ET-3014"))["items"]
    by_number = {item["article_number"]: item for item in items}
    assert by_number["ET-3014"]["is_active"] is False


def test_inactive_product_found_by_text_is_flagged(catalog: Any, tool_call: Any) -> None:
    items = structured(_search(tool_call, "Steuerung Typ alt", limit=20))["items"]
    by_number = {item["article_number"]: item for item in items}
    assert by_number["ET-3014"]["is_active"] is False


def test_inactive_products_come_after_active_ones(make: Any, tool_call: Any) -> None:
    """Der inaktive Artikel hat die kleinere Artikelnummer und den gleichen Text: Er steht trotzdem hinten."""
    make.product(article_number="AB-0001", name="Zebrabandtest", is_active=False)
    make.product(article_number="AB-0002", name="Zebrabandtest")
    items = structured(_search(tool_call, "Zebrabandtest"))["items"]
    assert [(i["article_number"], i["is_active"]) for i in items] == [
        ("AB-0002", True),
        ("AB-0001", False),
    ]


def test_equal_rank_is_ordered_by_article_number(make: Any, tool_call: Any) -> None:
    for number in ("AB-0003", "AB-0001", "AB-0002"):
        make.product(article_number=number, name="Quarkband")
    assert _numbers(_search(tool_call, "Quarkband")) == ["AB-0001", "AB-0002", "AB-0003"]


def test_order_exact_match_first_even_if_inactive_then_active_before_inactive(
    make: Any, catalog: Any, tool_call: Any
) -> None:
    """Gesamte Reihenfolge am Beispiel des ausgelaufenen ET-3014 (is_active = false, Nachfolger ET-3010).

    1. exakter Treffer, auch wenn inaktiv (gekennzeichnet);
    2. aktive Artikel, darin zuerst Präfix-, dann Volltexttreffer;
    3. inaktive Artikel, darin ebenso Präfix vor Volltext;
    innerhalb einer Gruppe nach Rang, bei gleichem Rang nach Artikelnummer.

    Zu ET-3014 aus dem Katalog kommen in der Testtransaktion hinzu: je ein aktiver und ein inaktiver
    Präfixtreffer (Varianten) und je Gruppe Volltexttreffer, die ET-3014 im Text nennen. Die beiden aktiven
    Volltexttreffer sind textgleich und absichtlich in umgekehrter Reihenfolge angelegt (Tie-Breaker).
    """
    make.product(
        article_number="ET-3014-X2", name="Variante zwei", description="Neutral", is_active=False
    )
    make.product(article_number="ET-3014-X1", name="Variante eins", description="Neutral")
    mention = "Ersatz zu ET-3014 und weiteren Steuerungen"
    make.product(
        article_number="AB-9002", name="Ersatzhinweis", description=mention, is_active=False
    )
    make.product(article_number="AB-9003", name="Ersatzhinweis", description=mention)
    make.product(article_number="AB-9001", name="Ersatzhinweis", description=mention)

    items = structured(_search(tool_call, "ET-3014", limit=20))["items"]
    assert [(i["article_number"], i["is_active"]) for i in items] == [
        ("ET-3014", False),
        ("ET-3014-X1", True),
        ("AB-9001", True),
        ("AB-9003", True),
        ("ET-3014-X2", False),
        ("AB-9002", False),
    ]


def test_inactive_article_is_listed_after_its_active_successor(
    catalog: Any, tool_call: Any
) -> None:
    """Echtdaten: ET-3014 nennt den Nachfolger ET-3010 im Text. Wer nach dem Frequenzumrichter fragt, sieht
    zuerst den aktiven ET-3010; der ausgelaufene ET-3014 folgt, gekennzeichnet."""
    items = structured(_search(tool_call, "Frequenzumrichter", limit=20))["items"]
    flags = {i["article_number"]: i["is_active"] for i in items}
    assert flags["ET-3010"] is True
    assert flags["ET-3014"] is False
    numbers = [i["article_number"] for i in items]
    assert numbers.index("ET-3010") < numbers.index("ET-3014")
    active_flags = [i["is_active"] for i in items]
    assert active_flags == sorted(active_flags, reverse=True), (
        "erst alle aktiven, dann alle inaktiven"
    )


# SQL-Metazeichen: Eingabe ist Daten, nie Teil der Abfrage


@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE products; --",
        "' OR '1'='1",
        '" OR "1"="1',
        "Gurtband'; DELETE FROM products; --",
        "\\",
        "'",
        '"',
        "--",
        "/* x */",
        "a:*",
        "(",
        ")",
        "!",
        "&",
        "|",
        "<->",
        "-",
        "%",
        "_",
        "FB-%",
        "F_-1001",
    ],
    ids=repr,
)
def test_sql_metacharacters_are_harmless(
    catalog: Any, tool_call: Any, conn: Any, query: str
) -> None:
    before = conn.execute("SELECT count(*) FROM products").fetchone()
    result = _search(tool_call, query)
    assert result.get("isError") is not True, error_text(result) if result.get("isError") else ""
    content = structured(result)
    assert content["count"] == len(content["items"])
    assert conn.execute("SELECT count(*) FROM products").fetchone() == before
    assert before is not None and before[0] > 0


@pytest.mark.parametrize(
    "query",
    ["'; DROP TABLE products; --", "\\", "%", "_", "%%", "FB-%", "F_-1001", "FB-1001' AND '1'='2"],
    ids=repr,
)
def test_wildcards_and_injection_match_nothing(catalog: Any, tool_call: Any, query: str) -> None:
    """% und _ sind keine Platzhalter (starts_with statt LIKE), Anführungszeichen beenden nichts."""
    assert structured(_search(tool_call, query, limit=20))["items"] == []


# Ungültige Eingabe: feste Meldung, kein Echo


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "x" * 201,
        "a\x00b",
        "a\nb",
        "a\tb",
        "a\x1bb",
        SECRET_MARKER + "x" * 200,
    ],
    ids=[
        "leer",
        "leerzeichen",
        "zu-lang",
        "nul",
        "zeilenumbruch",
        "tabulator",
        "escape",
        "marker-zu-lang",
    ],
)
def test_invalid_query_is_rejected_with_the_fixed_message(
    catalog: Any, tool_call: Any, query: str
) -> None:
    from hoffmann_data.db import DATABASE_ERROR
    from hoffmann_data.limits import QUERY_INVALID

    result = _search(tool_call, query)
    text = error_text(result)
    assert QUERY_INVALID in text
    assert DATABASE_ERROR not in text, "NUL und Steuerzeichen dürfen die Datenbank nicht erreichen"
    assert SECRET_MARKER not in text
    assert not result.get("structuredContent")


def test_query_of_exactly_200_characters_is_accepted(catalog: Any, tool_call: Any) -> None:
    result = _search(tool_call, "x" * 200)
    assert structured(result) == {"items": [], "count": 0}


def test_missing_query_is_an_error(catalog: Any, tool_call: Any) -> None:
    assert tool_call("search_products", {}).get("isError") is True


@pytest.mark.parametrize(
    ("value", "early_rejection_allowed"),
    [
        # Ein einzelnes Surrogat ist kein gültiges JSON/UTF-8: Transport oder SDK dürfen es vor dem
        # Werkzeug abweisen. Der Test belegt dann nur, dass die Datenbank nicht erreicht wird.
        pytest.param("a\ud800b", True, id="surrogat"),
        # Der Zeilentrenner ist gültiges JSON: Er muss das Werkzeug erreichen und dort abgelehnt werden.
        pytest.param("a\U00002028b", False, id="zeilentrenner"),
    ],
)
def test_surrogate_and_line_separator_do_not_reach_the_database(
    catalog: Any, raw_tool_call: Any, value: str, early_rejection_allowed: bool
) -> None:
    from hoffmann_data.db import DATABASE_ERROR
    from hoffmann_data.limits import QUERY_INVALID

    response = raw_tool_call("search_products", {"query": value})
    check_rejected_before_database(
        response, QUERY_INVALID, DATABASE_ERROR, early_rejection_allowed=early_rejection_allowed
    )


# Limit: Standard 5, Grenzen 1 bis 20


@pytest.fixture
def many_products(make: Any) -> None:
    for n in range(25):
        make.product(name=f"Massentest {n}")


def test_default_limit_is_5(many_products: None, tool_call: Any) -> None:
    content = structured(_search(tool_call, "Massentest"))
    assert len(content["items"]) == 5 and content["count"] == 5


@pytest.mark.parametrize(("limit", "expected"), [(1, 1), (2, 2), (20, 20)])
def test_limit_inside_the_bounds_is_applied(
    many_products: None, tool_call: Any, limit: int, expected: int
) -> None:
    content = structured(_search(tool_call, "Massentest", limit=limit))
    assert len(content["items"]) == expected and content["count"] == expected


@pytest.mark.parametrize("limit", [0, -1, 21, 1000])
def test_limit_outside_the_bounds_is_rejected(
    many_products: None, tool_call: Any, limit: int
) -> None:
    from hoffmann_data.limits import LIMIT_INVALID

    result = _search(tool_call, "Massentest", limit=limit)
    assert LIMIT_INVALID in error_text(result)
    assert not result.get("structuredContent")


def test_count_is_the_number_of_returned_items_not_of_all_matches(
    many_products: None, tool_call: Any
) -> None:
    assert structured(_search(tool_call, "Massentest", limit=3))["count"] == 3


# Messtest: Typen von limit und query (gewünschtes Verhalten; Etappe 4 belegt, ob die einfache Signatur genügt)


@pytest.mark.parametrize(
    "limit",
    ["7", True, 7.0, 7.5, "abc", None, SECRET_MARKER],
    ids=["text-7", "true", "7.0", "7.5", "text-abc", "null", "marker"],
)
def test_limit_type_coercion(many_products: None, tool_call: Any, limit: Any) -> None:
    """Nur ein echtes int ist gültig: nicht "7", nicht true, nicht 7.0, nicht 7.5, nicht null.

    Die Meldung ist die feste Meldung des Dienstes (nicht der Text des SDKs), ohne Echo des Wertes.
    """
    from hoffmann_data.limits import LIMIT_INVALID

    result = tool_call("search_products", {"query": "Massentest", "limit": limit})
    text = error_text(result)
    assert LIMIT_INVALID in text
    assert SECRET_MARKER not in text
    assert "7.5" not in text and "abc" not in text
    assert not result.get("structuredContent")


@pytest.mark.parametrize(
    "query",
    [123, True, 1.5, None, ["a"], [SECRET_MARKER], {"a": SECRET_MARKER}],
    ids=["zahl", "true", "kommazahl", "null", "liste", "liste-marker", "objekt-marker"],
)
def test_query_type_coercion(catalog: Any, tool_call: Any, query: Any) -> None:
    from hoffmann_data.limits import QUERY_INVALID

    result = tool_call("search_products", {"query": query})
    text = error_text(result)
    assert QUERY_INVALID in text
    assert SECRET_MARKER not in text
    assert not result.get("structuredContent")


# Kein Geheimnis, nur lesen


def test_query_is_not_echoed_in_response_log_or_output(
    catalog: Any,
    tool_call: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    result = _search(tool_call, SECRET_MARKER)
    assert structured(result) == {"items": [], "count": 0}
    invalid = _search(tool_call, SECRET_MARKER + "x" * 200)
    assert error_text(invalid)
    captured = capsys.readouterr()
    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for text in (str(result), str(invalid), logged, captured.out, captured.err):
        assert SECRET_MARKER not in text


def test_search_changes_nothing_in_the_database(catalog: Any, tool_call: Any, conn: Any) -> None:
    def counts() -> list[Any]:
        return [
            conn.execute(f"SELECT count(*) FROM {table}").fetchone() for table in KNOWN_FIVE_TABLES
        ]

    before = counts()
    _search(tool_call, "Gurtband", limit=20)
    _search(tool_call, "'; DROP TABLE products; --")
    assert counts() == before
