"""Tests des Werkzeugs find_customer (F07, Auftrag 4.4 und 6.4): Kunde über Firma, E-Mail oder Domain, mit Historie.

Die Tests laufen durch die ganze App gegen PostgreSQL unter der lesenden Rolle (Fixture tool_call) und
brauchen TEST_DATABASE_URL, bis auf die Tests von pick_unique. Die Daten legt jeder Test selbst an
(Fixture make); die synthetischen Stammdaten enthalten übrigens keinen Kunden ohne Aktivitäten, deshalb
reicht für diesen Fall kein Blick in customers.json.

Festgelegt (docs/plans/F07-stand.md, Abschnitt c und d):
- Eingabe mit "@": Kontakt über die E-Mail-Adresse (matched_by "email"), sonst Kunde über die Domain der
  Adresse (matched_by "domain"). Ohne "@": Firmenname (matched_by "company"), sonst, wenn die Eingabe wie
  eine Domain aussieht, Domain (matched_by "domain"). Immer exakt, ohne Beachtung der Groß-/Kleinschreibung;
  Subdomains und Teilnamen treffen nicht.
- Kein Treffer: {"customer": null, "matched_by": null}, kein Fehler. Bis 20 Kontakte und die letzten 10
  Aktivitäten (neueste zuerst). Keine internen IDs, Beträge als Text mit zwei Nachkommastellen.
- Mehrdeutigkeit ist ein fester Fehler, nie Raten. Heute ist sie durch UNIQUE-Constraints auf domain, email
  und lower(company_name) in der Datenbank unmöglich; deshalb prüft ein Unit-Test pick_unique direkt.
- Die Namen der Tests mit Groß-/Kleinschreibung nutzen ASCII: lower() in PostgreSQL folgt dem Locale der
  Datenbank und wandelt Umlaute nicht in jeder Einstellung um.
"""

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import (
    SECRET_MARKER,
    assert_no_internal_ids,
    check_rejected_before_database,
    error_text,
    structured,
)

CUSTOMER_KEYS = {
    "company_name",
    "domain",
    "industry",
    "country",
    "status",
    "contacts",
    "recent_activities",
}
CONTACT_KEYS = {"first_name", "last_name", "email", "job_title", "language"}
ACTIVITY_KEYS = {
    "type",
    "occurred_at",
    "subject",
    "summary",
    "amount_eur",
    "created_by",
    "article_number",
}
NOT_FOUND = {"customer": None, "matched_by": None}
ALL_TABLES = ("products", "product_fits", "customers", "contacts", "activities")


def _find(tool_call: Any, query: Any) -> dict[str, Any]:
    return tool_call("find_customer", {"query": query})


def _alpha(make: Any) -> int:
    """Die Standardfirma der Tests: Firmenname, Domain und ein Kontakt mit bekannter Adresse."""
    customer = make.customer(
        company_name="Alpha Test GmbH",
        domain="alpha-test.example",
        industry="food",
        country="ch",
        status="existing",
    )
    make.contact(
        customer,
        first_name="Erika",
        last_name="Muster",
        email="erika.muster@alpha-test.example",
        job_title="Einkaufsleiterin",
        language="en",
    )
    return customer


# pick_unique (ohne Datenbank)


def test_pick_unique_gives_none_for_no_row() -> None:
    from hoffmann_data.crm import pick_unique

    assert pick_unique([]) is None


def test_pick_unique_gives_the_single_row() -> None:
    from hoffmann_data.crm import pick_unique

    row = {"company_name": "Alpha Test GmbH"}
    assert pick_unique([row]) is row


def test_pick_unique_never_guesses_between_several_rows() -> None:
    from hoffmann_data.crm import AMBIGUOUS_MESSAGE, pick_unique

    assert AMBIGUOUS_MESSAGE == "Mehrdeutiger Treffer."
    with pytest.raises(ToolError) as info:
        pick_unique([{"company_name": "Alpha Test GmbH"}, {"company_name": "Alpha Test AG"}])
    assert str(info.value) == AMBIGUOUS_MESSAGE
    assert "Alpha" not in str(info.value), "kein Wert in der Meldung"


# Die drei Wege


def test_find_by_company_name(make: Any, tool_call: Any) -> None:
    _alpha(make)
    content = structured(_find(tool_call, "Alpha Test GmbH"))
    assert content["matched_by"] == "company"
    assert content["customer"]["company_name"] == "Alpha Test GmbH"
    assert content["customer"]["domain"] == "alpha-test.example"


@pytest.mark.parametrize("variant", ["ALPHA TEST GMBH", "alpha test gmbh", "  Alpha Test GmbH  "])
def test_find_by_company_name_ignores_case_and_outer_spaces(
    make: Any, tool_call: Any, variant: str
) -> None:
    _alpha(make)
    content = structured(_find(tool_call, variant))
    assert content["matched_by"] == "company"
    assert content["customer"]["company_name"] == "Alpha Test GmbH"


def test_find_by_company_name_with_umlaut_and_the_same_spelling(make: Any, tool_call: Any) -> None:
    make.customer(company_name="Müller Förderanlagen GmbH", domain="mueller-foerder.example")
    content = structured(_find(tool_call, "Müller Förderanlagen GmbH"))
    assert content["customer"]["domain"] == "mueller-foerder.example"


def test_find_by_contact_email(make: Any, tool_call: Any) -> None:
    _alpha(make)
    content = structured(_find(tool_call, "erika.muster@alpha-test.example"))
    assert content["matched_by"] == "email"
    assert content["customer"]["company_name"] == "Alpha Test GmbH"
    assert [c["email"] for c in content["customer"]["contacts"]] == [
        "erika.muster@alpha-test.example"
    ]


@pytest.mark.parametrize(
    "variant", ["Erika.Muster@ALPHA-TEST.EXAMPLE", "  erika.muster@alpha-test.example "]
)
def test_find_by_email_ignores_case_and_outer_spaces(
    make: Any, tool_call: Any, variant: str
) -> None:
    _alpha(make)
    assert structured(_find(tool_call, variant))["matched_by"] == "email"


def test_find_by_unknown_email_falls_back_to_the_domain_of_the_address(
    make: Any, tool_call: Any
) -> None:
    """Absender ohne eigenen Kontakt: Die Firma wird über die Domain erkannt (Auftrag 4.7)."""
    _alpha(make)
    content = structured(_find(tool_call, "neu.kollege@alpha-test.example"))
    assert content["matched_by"] == "domain"
    assert content["customer"]["company_name"] == "Alpha Test GmbH"


def test_known_email_wins_over_the_domain_of_another_customer(make: Any, tool_call: Any) -> None:
    """Der Kontakt gehört zu Firma B, die Adresse hat die Domain von Firma A: Die Adresse entscheidet."""
    make.customer(company_name="Gamma A GmbH", domain="gamma-a.example")
    firm_b = make.customer(company_name="Gamma B GmbH", domain="gamma-b.example")
    make.contact(firm_b, email="wechsler@gamma-a.example")
    content = structured(_find(tool_call, "wechsler@gamma-a.example"))
    assert content["matched_by"] == "email"
    assert content["customer"]["company_name"] == "Gamma B GmbH"


@pytest.mark.parametrize(
    "variant", ["alpha-test.example", "ALPHA-TEST.EXAMPLE", " alpha-test.example "]
)
def test_find_by_domain(make: Any, tool_call: Any, variant: str) -> None:
    _alpha(make)
    content = structured(_find(tool_call, variant))
    assert content["matched_by"] == "domain"
    assert content["customer"]["company_name"] == "Alpha Test GmbH"


def test_company_name_wins_over_a_domain_when_both_would_match(make: Any, tool_call: Any) -> None:
    """Ohne "@" gilt zuerst der Firmenname; die Domain ist nur der zweite Weg."""
    make.customer(company_name="beta-test.example", domain="beta-eins.example")
    make.customer(company_name="Beta Zwei GmbH", domain="beta-test.example")
    content = structured(_find(tool_call, "beta-test.example"))
    assert content["matched_by"] == "company"
    assert content["customer"]["domain"] == "beta-eins.example"


# Kein Treffer: null, kein Fehler


@pytest.mark.parametrize(
    "query",
    [
        "Unbekannte Firma GmbH",
        "Alpha",
        "Alpha Test",
        "Test GmbH",
        "mail.alpha-test.example",
        "www.alpha-test.example",
        "kollege@mail.alpha-test.example",
        "alpha-test.example.evil.example",
        "alpha-test.example/pfad",
        "unbekannt@fremd.example",
        "fremd.example",
    ],
)
def test_no_match_gives_null_and_no_error(make: Any, tool_call: Any, query: str) -> None:
    """Nur exakte Treffer: weder Teilnamen noch Subdomains noch Ähnliches. Nichts wird geraten."""
    _alpha(make)
    result = _find(tool_call, query)
    assert result.get("isError") is not True
    assert structured(result) == NOT_FOUND


def test_no_match_in_an_empty_database(tool_call: Any) -> None:
    assert structured(_find(tool_call, "Alpha Test GmbH")) == NOT_FOUND


# Ähnliche Firmen dürfen nicht verwechselt werden (Absender-Abgleich, Auftrag 4.7)


@pytest.fixture
def hartmann(make: Any) -> None:
    verarbeitung = make.customer(
        company_name="Hartmann Metallverarbeitung GmbH",
        domain="hartmann-metallverarbeitung.example",
        status="existing",
        country="de",
    )
    bearbeitung = make.customer(
        company_name="Hartmann Metallbearbeitung GmbH",
        domain="hartmann-metallbearbeitung.example",
        status="lead",
        country="at",
    )
    make.contact(
        verarbeitung,
        first_name="Bernd",
        last_name="Hartmann",
        email="bernd@hartmann-metallverarbeitung.example",
    )
    make.contact(
        bearbeitung,
        first_name="Markus",
        last_name="Hartmann",
        email="markus@hartmann-metallbearbeitung.example",
    )


@pytest.mark.parametrize(
    ("query", "company", "matched_by"),
    [
        ("Hartmann Metallverarbeitung GmbH", "Hartmann Metallverarbeitung GmbH", "company"),
        ("Hartmann Metallbearbeitung GmbH", "Hartmann Metallbearbeitung GmbH", "company"),
        ("hartmann-metallverarbeitung.example", "Hartmann Metallverarbeitung GmbH", "domain"),
        ("hartmann-metallbearbeitung.example", "Hartmann Metallbearbeitung GmbH", "domain"),
        ("bernd@hartmann-metallverarbeitung.example", "Hartmann Metallverarbeitung GmbH", "email"),
        ("markus@hartmann-metallbearbeitung.example", "Hartmann Metallbearbeitung GmbH", "email"),
        (
            "sekretariat@hartmann-metallbearbeitung.example",
            "Hartmann Metallbearbeitung GmbH",
            "domain",
        ),
    ],
)
def test_similar_companies_are_not_mixed_up(
    hartmann: None, tool_call: Any, query: str, company: str, matched_by: str
) -> None:
    content = structured(_find(tool_call, query))
    assert content["customer"]["company_name"] == company
    assert content["matched_by"] == matched_by


@pytest.mark.parametrize(
    "query",
    [
        "Hartmann Metallverarbeitung",
        "Hartmann GmbH",
        "Hartmann",
        "hartmann-metall.example",
        "hartmann-metallverarbeitung.example.example",
        "info@hartmann-metall.example",
    ],
)
def test_near_misses_of_similar_companies_give_no_match(
    hartmann: None, tool_call: Any, query: str
) -> None:
    assert structured(_find(tool_call, query)) == NOT_FOUND


# Form der Antwort


def test_result_has_exactly_customer_and_matched_by(make: Any, tool_call: Any) -> None:
    _alpha(make)
    content = structured(_find(tool_call, "Alpha Test GmbH"))
    assert set(content) == {"customer", "matched_by"}, "kein Feld ambiguous"


def test_customer_has_exactly_the_documented_fields(make: Any, tool_call: Any) -> None:
    _alpha(make)
    customer = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]
    assert set(customer) == CUSTOMER_KEYS
    assert customer["industry"] == "food"
    assert customer["country"] == "ch"
    assert customer["status"] == "existing"


def test_contacts_have_exactly_the_documented_fields(make: Any, tool_call: Any) -> None:
    _alpha(make)
    contacts = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["contacts"]
    assert contacts == [
        {
            "first_name": "Erika",
            "last_name": "Muster",
            "email": "erika.muster@alpha-test.example",
            "job_title": "Einkaufsleiterin",
            "language": "en",
        }
    ]
    assert set(contacts[0]) == CONTACT_KEYS


def test_contact_without_job_title_has_null(make: Any, tool_call: Any) -> None:
    customer = make.customer(company_name="Delta Test GmbH", domain="delta-test.example")
    make.contact(customer, email="a@delta-test.example")
    contacts = structured(_find(tool_call, "Delta Test GmbH"))["customer"]["contacts"]
    assert contacts[0]["job_title"] is None


def test_search_by_one_email_returns_all_contacts_of_the_company(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    make.contact(customer, email="zweiter@alpha-test.example")
    content = structured(_find(tool_call, "erika.muster@alpha-test.example"))
    emails = {c["email"] for c in content["customer"]["contacts"]}
    assert emails == {"erika.muster@alpha-test.example", "zweiter@alpha-test.example"}


def test_result_has_no_internal_ids(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    product = make.product(article_number="AB-0200")
    make.activity(customer, product_id=product)
    for query in ("Alpha Test GmbH", "erika.muster@alpha-test.example", "alpha-test.example"):
        assert_no_internal_ids(structured(_find(tool_call, query)))


@pytest.mark.parametrize("status", ["existing", "lead", "inactive"])
def test_every_customer_status_is_returned(make: Any, tool_call: Any, status: str) -> None:
    """Auch ein inaktiver Kunde ist kein Nichttreffer: Er ist kein Neukunde, aber kein aktiver Bestandskunde."""
    make.customer(company_name="Status Test GmbH", domain="status-test.example", status=status)
    assert structured(_find(tool_call, "Status Test GmbH"))["customer"]["status"] == status


# Kontakte und Aktivitäten


def test_customer_without_activities_has_an_empty_history(make: Any, tool_call: Any) -> None:
    _alpha(make)
    customer = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]
    assert customer["recent_activities"] == []


def test_customer_without_contacts_has_an_empty_contact_list(make: Any, tool_call: Any) -> None:
    make.customer(company_name="Ohne Kontakt GmbH", domain="ohne-kontakt.example")
    customer = structured(_find(tool_call, "Ohne Kontakt GmbH"))["customer"]
    assert customer["contacts"] == []
    assert customer["recent_activities"] == []


def test_activity_has_exactly_the_documented_fields_and_values(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    product = make.product(article_number="AB-0200")
    make.activity(
        customer,
        type="quote",
        occurred_at="2026-03-01T10:00:00+00",
        subject="Angebot Gurtförderer",
        summary="Angebot über 2 Meter",
        amount_eur=1780,
        created_by="staff",
        product_id=product,
    )
    activities = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["recent_activities"]
    assert len(activities) == 1
    activity = activities[0]
    assert set(activity) == ACTIVITY_KEYS
    assert activity["type"] == "quote"
    assert activity["subject"] == "Angebot Gurtförderer"
    assert activity["summary"] == "Angebot über 2 Meter"
    assert activity["amount_eur"] == "1780.00"
    assert activity["created_by"] == "staff"
    assert activity["article_number"] == "AB-0200"
    assert datetime.fromisoformat(activity["occurred_at"]) == datetime(
        2026, 3, 1, 10, 0, tzinfo=UTC
    )


def test_activity_without_amount_and_product_has_nulls(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    make.activity(customer, type="note", subject="Notiz", summary="Nur eine Notiz")
    activity = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["recent_activities"][0]
    assert activity["amount_eur"] is None
    assert activity["article_number"] is None


def test_amounts_are_text_with_two_decimals(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    make.activity(customer, type="order", amount_eur=12.5, occurred_at="2026-01-01T10:00:00+00")
    make.activity(customer, type="order", amount_eur=0, occurred_at="2026-01-02T10:00:00+00")
    amounts = [
        a["amount_eur"]
        for a in structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["recent_activities"]
    ]
    assert amounts == ["0.00", "12.50"]


def test_only_the_ten_newest_activities_newest_first(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    # absichtlich nicht in zeitlicher Reihenfolge eingefügt: sortiert wird nach occurred_at
    for day in (5, 1, 12, 8, 3, 11, 2, 9, 6, 10, 4, 7):
        make.activity(
            customer,
            occurred_at=f"2026-01-{day:02d}T10:00:00+00",
            subject=f"Eintrag {day}",
        )
    activities = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["recent_activities"]
    assert [a["subject"] for a in activities] == [f"Eintrag {d}" for d in range(12, 2, -1)]


def test_exactly_ten_activities_are_all_returned(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    for day in range(1, 11):
        make.activity(customer, occurred_at=f"2026-02-{day:02d}T10:00:00+00")
    activities = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["recent_activities"]
    assert len(activities) == 10


def test_at_most_twenty_contacts(make: Any, tool_call: Any) -> None:
    customer = _alpha(make)
    for n in range(21):
        make.contact(customer, email=f"kollege{n:02d}@alpha-test.example")
    contacts = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]["contacts"]
    assert len(contacts) == 20


def test_other_customers_data_is_not_mixed_in(make: Any, tool_call: Any) -> None:
    alpha = _alpha(make)
    other = make.customer(company_name="Fremd GmbH", domain="fremd-gmbh.example")
    make.contact(other, email="fremder@fremd-gmbh.example")
    make.activity(alpha, subject="gehört zu Alpha")
    make.activity(other, subject="gehört zu Fremd")
    customer = structured(_find(tool_call, "Alpha Test GmbH"))["customer"]
    assert [a["subject"] for a in customer["recent_activities"]] == ["gehört zu Alpha"]
    assert [c["email"] for c in customer["contacts"]] == ["erika.muster@alpha-test.example"]


# Ungültige Eingabe: feste Meldung, kein Echo


@pytest.mark.parametrize(
    "query",
    ["", "   ", "x" * 201, "a\x00b", "a\nb", "a\tb", SECRET_MARKER + "x" * 200],
    ids=["leer", "leerzeichen", "zu-lang", "nul", "zeilenumbruch", "tabulator", "marker-zu-lang"],
)
def test_invalid_query_is_rejected_with_the_fixed_message(
    make: Any, tool_call: Any, query: str
) -> None:
    from hoffmann_data.db import DATABASE_ERROR
    from hoffmann_data.limits import CUSTOMER_QUERY_INVALID

    _alpha(make)
    result = _find(tool_call, query)
    text = error_text(result)
    assert CUSTOMER_QUERY_INVALID in text
    assert DATABASE_ERROR not in text, "NUL und Steuerzeichen dürfen die Datenbank nicht erreichen"
    assert SECRET_MARKER not in text
    assert not result.get("structuredContent")


def test_query_of_exactly_200_characters_is_accepted(make: Any, tool_call: Any) -> None:
    _alpha(make)
    assert structured(_find(tool_call, "x" * 200)) == NOT_FOUND


@pytest.mark.parametrize(
    "query",
    [123, True, 1.5, None, ["a"], [SECRET_MARKER], {"a": SECRET_MARKER}],
    ids=["zahl", "true", "kommazahl", "null", "liste", "liste-marker", "objekt-marker"],
)
def test_query_must_be_text(make: Any, tool_call: Any, query: Any) -> None:
    from hoffmann_data.limits import CUSTOMER_QUERY_INVALID

    _alpha(make)
    result = _find(tool_call, query)
    text = error_text(result)
    assert CUSTOMER_QUERY_INVALID in text
    assert SECRET_MARKER not in text
    assert not result.get("structuredContent")


def test_missing_query_is_an_error(make: Any, tool_call: Any) -> None:
    _alpha(make)
    assert tool_call("find_customer", {}).get("isError") is True


@pytest.mark.parametrize(
    ("value", "early_rejection_allowed"),
    [
        # Ein einzelnes Surrogat ist kein gültiges JSON/UTF-8: Transport oder SDK dürfen es vor dem
        # Werkzeug abweisen. Der Test belegt dann nur, dass die Datenbank nicht erreicht wird.
        pytest.param("a\ud800b", True, id="surrogat"),
        # Der Zeilentrenner ist gültiges JSON: Er muss das Werkzeug erreichen und dort abgelehnt werden.
        pytest.param("a b", False, id="zeilentrenner"),
    ],
)
def test_surrogate_and_line_separator_do_not_reach_the_database(
    make: Any, raw_tool_call: Any, value: str, early_rejection_allowed: bool
) -> None:
    from hoffmann_data.db import DATABASE_ERROR
    from hoffmann_data.limits import CUSTOMER_QUERY_INVALID

    _alpha(make)
    response = raw_tool_call("find_customer", {"query": value})
    check_rejected_before_database(
        response,
        CUSTOMER_QUERY_INVALID,
        DATABASE_ERROR,
        early_rejection_allowed=early_rejection_allowed,
    )


# SQL-Metazeichen


@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE customers; --",
        "Alpha Test GmbH' OR '1'='1",
        "x@alpha-test.example' OR '1'='1",
        "%",
        "_",
        "%@%",
        "Alpha%",
        "Alp_a Test GmbH",
        "%.example",
        "\\",
        "'",
    ],
    ids=repr,
)
def test_sql_metacharacters_are_harmless(make: Any, tool_call: Any, conn: Any, query: str) -> None:
    """Eingabe ist Daten: % und _ sind keine Platzhalter, Anführungszeichen beenden nichts."""
    _alpha(make)
    before = conn.execute("SELECT count(*) FROM customers").fetchone()
    result = _find(tool_call, query)
    assert result.get("isError") is not True
    assert structured(result) == NOT_FOUND
    assert conn.execute("SELECT count(*) FROM customers").fetchone() == before


# Kein Geheimnis, nur lesen


def test_input_is_not_echoed_in_response_log_or_output(
    make: Any,
    tool_call: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _alpha(make)
    caplog.set_level(logging.DEBUG)
    unknown = _find(tool_call, SECRET_MARKER)
    invalid = _find(tool_call, SECRET_MARKER + "x" * 200)
    assert structured(unknown) == NOT_FOUND
    assert error_text(invalid)
    captured = capsys.readouterr()
    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for text in (str(unknown), str(invalid), logged, captured.out, captured.err):
        assert SECRET_MARKER not in text


def test_find_customer_changes_nothing_and_creates_no_lead(
    make: Any, tool_call: Any, conn: Any
) -> None:
    """Der Abgleich legt nichts an (Leads kommen mit F08, Dubletten mit F08)."""

    def counts() -> list[Any]:
        return [conn.execute(f"SELECT count(*) FROM {table}").fetchone() for table in ALL_TABLES]

    _alpha(make)
    before = counts()
    _find(tool_call, "Alpha Test GmbH")
    _find(tool_call, "unbekannt@fremd.example")
    _find(tool_call, "'; DROP TABLE customers; --")
    assert counts() == before
