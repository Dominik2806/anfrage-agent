"""Tests der Eingabeprüfung der Lese-Werkzeuge: hoffmann_data/limits.py (F07, Auftrag 11.3).

Jede Prüfung gibt den bereinigten Wert zurück oder wirft einen ToolError mit fester Meldung. Die Meldung
enthält nie den Eingabewert (Kundentext ist Daten, kein Echo). Ohne Datenbank und ohne Netzwerk.
"""

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import SECRET_MARKER

CONTROL_CHARACTERS = ["\x00", "\n", "\r", "\t", "\x1b", "\x7f", "\x85"]


def test_limit_constants() -> None:
    from hoffmann_data import limits

    assert limits.MAX_QUERY_LENGTH == 200
    assert limits.DEFAULT_LIMIT == 5
    assert limits.MAX_LIMIT == 20


def test_messages_are_fixed_distinct_and_free_of_values() -> None:
    from hoffmann_data import limits

    messages = [
        limits.QUERY_INVALID,
        limits.LIMIT_INVALID,
        limits.ARTICLE_NUMBER_INVALID,
        limits.CUSTOMER_QUERY_INVALID,
    ]
    assert all(isinstance(m, str) and m.strip() for m in messages)
    assert len(set(messages)) == len(messages)
    assert all(SECRET_MARKER not in m for m in messages)


# check_query


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Gurtband", "Gurtband"),
        ("  Gurtband  ", "Gurtband"),
        ("Gurtförderer Standard", "Gurtförderer Standard"),
        ("x", "x"),
        ("x" * 200, "x" * 200),
        ("  " + "x" * 200 + "  ", "x" * 200),
        ("ä" * 200, "ä" * 200),
        ("'; DROP TABLE products; --", "'; DROP TABLE products; --"),
        ("100 % Edelstahl_V2", "100 % Edelstahl_V2"),
    ],
    ids=[
        "normal",
        "mit-leerzeichen-aussen",
        "umlaut",
        "ein-zeichen",
        "genau-200",
        "200-nach-strip",
        "200-umlaute-zeichen-nicht-bytes",
        "sql-metazeichen",
        "prozent-unterstrich",
    ],
)
def test_check_query_accepts_and_strips(value: str, expected: str) -> None:
    from hoffmann_data.limits import check_query

    assert check_query(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "x" * 201,
        " " + "x" * 201 + " ",
        "ä" * 201,
        *[f"vor{c}nach" for c in CONTROL_CHARACTERS],
        *[f"{c}" for c in CONTROL_CHARACTERS if c.strip()],
        SECRET_MARKER + "x" * 200,
    ],
)
def test_check_query_rejects_with_the_fixed_message(value: str) -> None:
    from hoffmann_data.limits import QUERY_INVALID, check_query

    with pytest.raises(ToolError) as info:
        check_query(value)
    assert str(info.value) == QUERY_INVALID
    assert SECRET_MARKER not in str(info.value)


@pytest.mark.parametrize("value", [None, 123, 1.5, True, ["a"], {"a": 1}, b"abc"])
def test_check_query_rejects_non_strings(value: Any) -> None:
    from hoffmann_data.limits import QUERY_INVALID, check_query

    with pytest.raises(ToolError) as info:
        check_query(value)
    assert str(info.value) == QUERY_INVALID


# check_limit


@pytest.mark.parametrize("value", [1, 2, 5, 19, 20])
def test_check_limit_accepts_1_to_20(value: int) -> None:
    from hoffmann_data.limits import check_limit

    assert check_limit(value) == value


@pytest.mark.parametrize(
    "value",
    [0, -1, 21, 100, 10**12, "7", "abc", "", None, True, False, 7.0, 7.5, [7], {"limit": 7}],
    ids=repr,
)
def test_check_limit_rejects_everything_else_with_the_fixed_message(value: Any) -> None:
    """Streng: nur ein echtes int (kein bool, keine Zahl als Text, keine Kommazahl)."""
    from hoffmann_data.limits import LIMIT_INVALID, check_limit

    with pytest.raises(ToolError) as info:
        check_limit(value)
    assert str(info.value) == LIMIT_INVALID


def test_check_limit_does_not_echo_the_value() -> None:
    from hoffmann_data.limits import check_limit

    with pytest.raises(ToolError) as info:
        check_limit(SECRET_MARKER)
    assert SECRET_MARKER not in str(info.value)


# check_article_number


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("FB-1001", "FB-1001"),
        ("fb-1001", "FB-1001"),
        ("  FB-1001  ", "FB-1001"),
        ("FB-1001\n", "FB-1001"),
        ("FB-1001-B8", "FB-1001-B8"),
        ("fb-1001-b8", "FB-1001-B8"),
        ("ET-3014", "ET-3014"),
        ("WV-4005", "WV-4005"),
        ("AB-0000-ABCD", "AB-0000-ABCD"),
        ("AB-0000-1", "AB-0000-1"),
    ],
    ids=[
        "normal",
        "klein",
        "leerzeichen",
        "zeilenumbruch-aussen",
        "variante",
        "variante-klein",
        "ersatzteil",
        "wartungsvertrag",
        "suffix-4-zeichen",
        "suffix-1-zeichen",
    ],
)
def test_check_article_number_accepts_and_normalises(value: str, expected: str) -> None:
    from hoffmann_data.limits import check_article_number

    assert check_article_number(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "FB1001",
        "FB-100",
        "FB-10012",
        "F-1001",
        "FBX-1001",
        "12-1001",
        "FB-ABCD",
        "FB-1001-",
        "FB-1001-ABCDE",
        "FB-1001-B8-X",
        "FB-1001 B8",
        "FB-1001\nFB-1002",
        "FB-1001\x00",
        "FB-1001'; DROP TABLE products; --",
        "FB-1001 OR 1=1",
        "FB-%",
        "FB-____",
        "ＦＢ-1001",
        "FB-١٠٠١",
        "FB‐1001",
        SECRET_MARKER,
        "FB-1001" + "0" * 200,
    ],
    ids=repr,
)
def test_check_article_number_rejects_with_the_fixed_message(value: str) -> None:
    from hoffmann_data.limits import ARTICLE_NUMBER_INVALID, check_article_number

    with pytest.raises(ToolError) as info:
        check_article_number(value)
    assert str(info.value) == ARTICLE_NUMBER_INVALID
    assert SECRET_MARKER not in str(info.value)


@pytest.mark.parametrize("value", [None, 1001, True, ["FB-1001"], b"FB-1001"])
def test_check_article_number_rejects_non_strings(value: Any) -> None:
    from hoffmann_data.limits import ARTICLE_NUMBER_INVALID, check_article_number

    with pytest.raises(ToolError) as info:
        check_article_number(value)
    assert str(info.value) == ARTICLE_NUMBER_INVALID


# check_customer_query


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Hartmann Metallverarbeitung GmbH", "Hartmann Metallverarbeitung GmbH"),
        ("  Hartmann Metallverarbeitung GmbH ", "Hartmann Metallverarbeitung GmbH"),
        (
            "einkauf@hartmann-metallverarbeitung.example",
            "einkauf@hartmann-metallverarbeitung.example",
        ),
        ("hartmann-metallverarbeitung.example", "hartmann-metallverarbeitung.example"),
        ("Müller & Söhne GmbH", "Müller & Söhne GmbH"),
        ("x", "x"),
        ("x" * 200, "x" * 200),
        ("Robert'); DROP TABLE customers;--", "Robert'); DROP TABLE customers;--"),
    ],
    ids=[
        "firma",
        "firma-strip",
        "email",
        "domain",
        "umlaut-und",
        "ein-zeichen",
        "genau-200",
        "sql",
    ],
)
def test_check_customer_query_accepts_and_strips(value: str, expected: str) -> None:
    from hoffmann_data.limits import check_customer_query

    assert check_customer_query(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "x" * 201,
        *[f"vor{c}nach" for c in CONTROL_CHARACTERS],
        SECRET_MARKER + "x" * 200,
    ],
)
def test_check_customer_query_rejects_with_the_fixed_message(value: str) -> None:
    from hoffmann_data.limits import CUSTOMER_QUERY_INVALID, check_customer_query

    with pytest.raises(ToolError) as info:
        check_customer_query(value)
    assert str(info.value) == CUSTOMER_QUERY_INVALID
    assert SECRET_MARKER not in str(info.value)


@pytest.mark.parametrize("value", [None, 123, True, ["a"], {"a": 1}, b"abc"])
def test_check_customer_query_rejects_non_strings(value: Any) -> None:
    from hoffmann_data.limits import CUSTOMER_QUERY_INVALID, check_customer_query

    with pytest.raises(ToolError) as info:
        check_customer_query(value)
    assert str(info.value) == CUSTOMER_QUERY_INVALID
