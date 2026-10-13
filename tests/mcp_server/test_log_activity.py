"""Tests des Werkzeugs log_activity (F08 Etappe 4b, ADR 0005 Punkt 7): Aktivität zu einem Kunden protokollieren.

Schnittstelle, die diese Tests festlegen:
- hoffmann_data.crm.log_activity(database, customer_domain, activity_type, subject, summary, contact_email=None)
  -> {"customer_domain": <klein>, "type": <Typ>, "logged": True}. Keine interne ID im Ergebnis.
- Reihenfolge: Eingaben prüfen (Parameterreihenfolge customer_domain, type, subject, summary, contact_email), dann die
  Datenbank (write_connection): Kunde über die Domain (limits.CUSTOMER_NOT_FOUND), Kontakt über Adresse UND Kunde
  (limits.CONTACT_NOT_FOUND, dieselbe Meldung für "unbekannt" und "gehört zu einem anderen Kunden"), dann ein INSERT.
- Es wird genau eine Zeile in activities geschrieben: created_by 'agent', occurred_at = now() der Datenbank,
  contact_id nur mit gefundenem Kontakt, kein product_id, kein amount_eur. Kunde und Kontakt werden nie verändert.
- contact_email=None heißt "kein Kontakt"; ein leerer Text ist angegeben und ungültig. Ein inaktiver Kunde ist erlaubt;
  ein zweiter gleicher Aufruf legt eine zweite Zeile an.
- Eine Transaktion je Aufruf: scheitert etwas nach dem INSERT, bleibt nichts stehen. Fehler der Datenbank ergeben die feste
  Meldung db.DATABASE_ERROR, ohne Datenbank db.NOT_CONFIGURED.
- Werkzeug auf /mcp-write: log_activity(customer_domain, type, subject, summary, contact_email=None); die Beschreibung
  ist kein Platzhalter mehr.

Mit "T" markierte Tests brauchen die Test-Datenbank (TEST_DATABASE_URL, nur localhost) und laufen unter der echten Rolle
aus db/roles/data_service_rw.sql (Fixture committed_rw_role, Anmeldung mit Passwort), nicht als Eigentümer. Die Zeilen
tragen den Marker aus mcp_testkit; marker_cleanup räumt vor und nach jedem Test auf. Mit "N" markierte brauchen keine.
Rot, bis crm.log_activity, db.insert_activity, der Werkzeugrumpf und die zwei Meldungen existieren (TDD).
"""

import json
import logging
import re
import uuid
from datetime import date
from typing import Any

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import (
    DB_SECRETS,
    MCP_PATH,
    RPC_HEADERS,
    SECRET_MARKER,
    WRITE_PATH,
    CountingDatabase,
    ExplodingDatabase,
    StatementRecorder,
    assert_no_internal_ids,
    error_text,
    parse_rpc_body,
    structured,
)
from psycopg import errors

DB_LOGGER = "hoffmann_data.db"
TOOL = "log_activity"
NUL = chr(0x00)
UNREACHABLE_URL = "postgresql://geheimuser:geheimpasswort@127.0.0.1:1/geheimdb"
INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"},
}
DEFAULTS: dict[str, Any] = {
    "customer_domain": "firma.example",
    "activity_type": "note",
    "subject": "Betreff",
    "summary": "Zusammenfassung",
    "contact_email": None,
}
MARKER_LOWER = SECRET_MARKER.lower()


def _limits() -> Any:
    from hoffmann_data import limits

    return limits


def _message(name: str) -> str:
    return str(getattr(_limits(), name))


def _call(database: Any, **overrides: Any) -> Any:
    from hoffmann_data import crm

    values = {**DEFAULTS, **overrides}
    return crm.log_activity(
        database,
        values["customer_domain"],
        values["activity_type"],
        values["subject"],
        values["summary"],
        values["contact_email"],
    )


def _error(database: Any, **overrides: Any) -> ToolError:
    """Der ToolError des Aufrufs; bricht ab, wenn der Aufruf gelingt."""
    with pytest.raises(ToolError) as info:
        _call(database, **overrides)
    return info.value


def _scalar(admin: Any, query: str, params: tuple[Any, ...]) -> Any:
    found = admin.execute(query, params).fetchone()
    assert found is not None
    return found[0]


def _customer_id(admin: Any, domain: str) -> int:
    return int(_scalar(admin, "SELECT id FROM customers WHERE domain = %s", (domain,)))


def _snapshot(admin: Any, domain: str) -> dict[str, Any]:
    """Kunde, Kontakte und die Zeilenzahlen von customers und contacts (zum Vergleich vor und nach dem Aufruf)."""
    from psycopg.rows import dict_row

    with admin.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM customers WHERE domain = %s", (domain,))
        customers = cursor.fetchall()
        cursor.execute(
            "SELECT * FROM contacts WHERE customer_id = (SELECT id FROM customers WHERE domain = %s) ORDER BY id",
            (domain,),
        )
        contacts = cursor.fetchall()
        cursor.execute(
            "SELECT (SELECT count(*) FROM customers) AS customers, (SELECT count(*) FROM contacts) AS contacts"
        )
        counts = cursor.fetchone()
    return {"customers": customers, "contacts": contacts, "counts": counts}


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)


def _db_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == DB_LOGGER]


def _unknown_domain() -> str:
    return f"mcp-schreibtest-unbekannt-{uuid.uuid4().hex[:12]}.example"


# Fixture-Selbstprüfung (T)


def test_the_write_database_runs_as_the_write_role(
    rw_write_database: Any, committed_rw_role: tuple[str, str]
) -> None:
    """Die Anmeldung gelingt als die echte Rolle: kein Superuser, keine Umgehung der Row Level Security."""
    name, _ = committed_rw_role
    with rw_write_database.connection() as connection:
        row = connection.execute(
            "SELECT current_user, r.rolsuper, r.rolbypassrls FROM pg_roles r WHERE r.rolname = current_user"
        ).fetchone()
    assert row == (name, False, False)


# 1. Erfolg (T)


def test_log_activity_writes_exactly_one_row_with_all_values(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    domain, _ = make_customer()
    _call(
        rw_write_database,
        customer_domain=domain,
        activity_type="inquiry",
        subject="  Anfrage Gurtband  ",
        summary="Zeile 1\n\tZeile 2\r\nZeile 3 ",
    )
    rows = activity_rows(domain)
    assert len(rows) == 1
    row = rows[0]
    assert row["customer_id"] == _customer_id(admin, domain)
    assert row["type"] == "inquiry"
    assert row["subject"] == "Anfrage Gurtband"
    assert row["summary"] == "Zeile 1\n\tZeile 2\r\nZeile 3"
    assert row["created_by"] == "agent"
    assert row["contact_id"] is None
    assert row["product_id"] is None
    assert row["amount_eur"] is None


def test_the_result_is_the_fixed_structure_without_internal_ids(
    rw_write_database: Any, make_customer: Any
) -> None:
    domain, _ = make_customer()
    result = _call(rw_write_database, customer_domain=domain)
    assert result == {"customer_domain": domain, "type": "note", "logged": True}
    assert_no_internal_ids(result)


def test_inputs_are_cleaned_before_the_lookup(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer()
    result = _call(rw_write_database, customer_domain="  " + domain.upper() + " ")
    assert result["customer_domain"] == domain
    assert len(activity_rows(domain)) == 1


def test_the_timestamp_comes_from_the_database(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, db_clock: Any
) -> None:
    domain, _ = make_customer()
    before = db_clock()
    _call(rw_write_database, customer_domain=domain)
    after = db_clock()
    assert before <= activity_rows(domain)[0]["occurred_at"] <= after


def test_the_insert_uses_now_and_sends_no_client_time(
    rw_write_database: Any, make_customer: Any
) -> None:
    """Der INSERT setzt occurred_at mit now() im SQL-Text; kein Parameter ist eine Zeitangabe des Clients."""
    domain, _ = make_customer()
    recorder = StatementRecorder(rw_write_database)
    _call(recorder, customer_domain=domain)
    inserts = [s for s in recorder.statements if s[0].lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1
    text, params = inserts[0]
    assert "now()" in text.lower()
    values = list(params.values()) if isinstance(params, dict) else list(params or ())
    assert not [v for v in values if isinstance(v, date | float)], values


def test_nothing_else_is_written_or_changed(
    rw_write_database: Any, make_customer: Any, admin: Any
) -> None:
    domain, email = make_customer()
    before = _snapshot(admin, domain)
    _call(rw_write_database, customer_domain=domain, contact_email=email)
    assert _snapshot(admin, domain) == before


def test_the_row_is_visible_to_other_connections_only_after_the_call(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer()
    seen: list[int] = []

    def after_insert(text: str, params: Any) -> None:
        if text.lstrip().upper().startswith("INSERT"):
            seen.append(len(activity_rows(domain)))

    _call(StatementRecorder(rw_write_database, on_execute=after_insert), customer_domain=domain)
    assert seen == [0], "mitten im Aufruf ist die Zeile für andere noch nicht sichtbar"
    assert len(activity_rows(domain)) == 1


# 2. Kontakt (T)


def test_with_contact_email_the_contact_id_is_set(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    domain, email = make_customer()
    assert email is not None
    _call(
        rw_write_database, customer_domain=domain, contact_email=email.upper()
    )  # wird klein gefunden
    contact_id = _scalar(admin, "SELECT id FROM contacts WHERE email = %s", (email,))
    assert activity_rows(domain)[0]["contact_id"] == contact_id


def test_without_contact_email_the_contact_id_stays_null(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, email = make_customer()
    assert email is not None, "der Kunde hat einen Kontakt, der Aufruf nennt ihn nicht"
    _call(rw_write_database, customer_domain=domain)
    assert activity_rows(domain)[0]["contact_id"] is None


def test_a_contact_of_another_customer_is_rejected_with_the_fixed_message(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer(contact=False)
    other_domain, other_email = make_customer()
    error = _error(rw_write_database, customer_domain=domain, contact_email=other_email)
    assert str(error) == _message("CONTACT_NOT_FOUND")
    assert other_email is not None and other_email not in str(error)
    assert activity_rows(domain) == [] and activity_rows(other_domain) == []


def test_an_unknown_contact_is_rejected_with_the_same_message(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer(contact=False)
    address = f"niemand-{uuid.uuid4().hex[:8]}@{domain}"
    error = _error(rw_write_database, customer_domain=domain, contact_email=address)
    assert str(error) == _message("CONTACT_NOT_FOUND")
    assert address not in str(error)
    assert activity_rows(domain) == []


def test_the_two_contact_cases_cannot_be_told_apart(
    rw_write_database: Any, make_customer: Any
) -> None:
    domain, _ = make_customer(contact=False)
    _, other_email = make_customer()
    foreign = _error(rw_write_database, customer_domain=domain, contact_email=other_email)
    unknown = _error(
        rw_write_database,
        customer_domain=domain,
        contact_email=f"niemand-{uuid.uuid4().hex[:8]}@{domain}",
    )
    assert type(foreign) is type(unknown)
    assert foreign.args == unknown.args
    assert foreign.__cause__ is None and unknown.__cause__ is None


def test_an_empty_contact_email_is_invalid_not_absent() -> None:
    """None heißt "kein Kontakt"; ein leerer Text ist angegeben und ungültig. Die Datenbank wird nicht berührt."""
    database = CountingDatabase()
    for value in ("", "   "):
        assert str(_error(database, contact_email=value)) == _message("EMAIL_INVALID")
    assert database.calls == 0


def test_the_customer_is_looked_up_before_the_contact(
    rw_write_database: Any, make_customer: Any
) -> None:
    _, other_email = make_customer()
    error = _error(rw_write_database, customer_domain=_unknown_domain(), contact_email=other_email)
    assert str(error) == _message("CUSTOMER_NOT_FOUND")


# 3. Unbekannter Kunde (T)


def test_an_unknown_customer_gives_the_fixed_message_without_the_domain(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer()
    unknown = _unknown_domain()
    for candidate in (
        unknown,
        f"mail.{domain}",
    ):  # eine Subdomain wird nicht mit dem Kunden zusammengeführt
        error = _error(rw_write_database, customer_domain=candidate)
        assert str(error) == _message("CUSTOMER_NOT_FOUND")
        assert candidate not in str(error)
    assert activity_rows(domain) == [] and activity_rows(unknown) == []


# 4. Nur drei Typen


@pytest.mark.parametrize(
    "value",
    [
        "call",
        "meeting",
        "order",
        "email",
        "quote",
        "service",
        "Inquiry",
        "NOTE",
        " note",
        "",
        None,
        5,
        True,
    ],
    ids=repr,
)
def test_other_types_are_rejected_before_the_database(value: Any) -> None:
    database = CountingDatabase()
    assert str(_error(database, activity_type=value)) == _message("ACTIVITY_TYPE_INVALID")
    assert database.calls == 0


@pytest.mark.parametrize("activity_type", ["inquiry", "complaint", "note"])
def test_the_three_types_are_accepted(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, activity_type: str
) -> None:
    domain, _ = make_customer()
    assert (
        _call(rw_write_database, customer_domain=domain, activity_type=activity_type)["type"]
        == activity_type
    )
    assert [r["type"] for r in activity_rows(domain)] == [activity_type]


# 5. Eingabeprüfung vor jedem Datenbankzugriff (N)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"customer_domain": "firma.de"}, "DOMAIN_INVALID", id="domain-nicht-example"),
        pytest.param({"customer_domain": "example"}, "DOMAIN_INVALID", id="domain-ohne-label"),
        pytest.param({"customer_domain": ""}, "DOMAIN_INVALID", id="domain-leer"),
        pytest.param({"customer_domain": None}, "DOMAIN_INVALID", id="domain-none"),
        pytest.param({"customer_domain": 5}, "DOMAIN_INVALID", id="domain-zahl"),
        pytest.param({"customer_domain": "firma.example" + NUL}, "DOMAIN_INVALID", id="domain-nul"),
        pytest.param({"subject": "x" * 201}, "SUBJECT_INVALID", id="betreff-zu-lang"),
        pytest.param({"subject": ""}, "SUBJECT_INVALID", id="betreff-leer"),
        pytest.param({"subject": "a" + NUL + "b"}, "SUBJECT_INVALID", id="betreff-nul"),
        pytest.param({"subject": None}, "SUBJECT_INVALID", id="betreff-none"),
        pytest.param({"summary": "x" * 2001}, "SUMMARY_INVALID", id="zusammenfassung-zu-lang"),
        pytest.param({"summary": "   "}, "SUMMARY_INVALID", id="zusammenfassung-leer"),
        pytest.param({"summary": "a" + NUL}, "SUMMARY_INVALID", id="zusammenfassung-nul"),
        pytest.param({"summary": ["a"]}, "SUMMARY_INVALID", id="zusammenfassung-liste"),
        pytest.param({"contact_email": "kein-at"}, "EMAIL_INVALID", id="adresse-ohne-at"),
        pytest.param(
            {"contact_email": "a" * 241 + "@firma.example"}, "EMAIL_INVALID", id="adresse-255"
        ),
        pytest.param({"contact_email": 5}, "EMAIL_INVALID", id="adresse-zahl"),
    ],
)
def test_invalid_inputs_never_touch_the_database(overrides: dict[str, Any], message: str) -> None:
    database = CountingDatabase()
    assert str(_error(database, **overrides)) == _message(message)
    assert database.calls == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param(
            {
                "customer_domain": "firma.de",
                "activity_type": "call",
                "subject": "",
                "summary": "",
                "contact_email": "x",
            },
            "DOMAIN_INVALID",
            id="alles-ungueltig",
        ),
        pytest.param(
            {"activity_type": "call", "subject": "", "summary": "", "contact_email": "x"},
            "ACTIVITY_TYPE_INVALID",
            id="ab-typ",
        ),
        pytest.param(
            {"subject": "", "summary": "", "contact_email": "x"}, "SUBJECT_INVALID", id="ab-betreff"
        ),
        pytest.param(
            {"summary": "", "contact_email": "x"}, "SUMMARY_INVALID", id="ab-zusammenfassung"
        ),
    ],
)
def test_inputs_are_checked_in_parameter_order(overrides: dict[str, Any], message: str) -> None:
    """customer_domain, type, subject, summary, contact_email: das erste ungültige Feld nennt die Meldung."""
    database = CountingDatabase()
    assert str(_error(database, **overrides)) == _message(message)
    assert database.calls == 0


def test_invalid_inputs_win_over_a_missing_database() -> None:
    from hoffmann_data.db import NOT_CONFIGURED

    assert str(_error(None, activity_type="call")) == _message("ACTIVITY_TYPE_INVALID")
    assert str(_error(None)) == NOT_CONFIGURED


# 6. Inaktiver Kunde, Wiederholung (T)


def test_an_inactive_customer_is_allowed(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer(status="inactive")
    assert _call(rw_write_database, customer_domain=domain)["logged"] is True
    assert len(activity_rows(domain)) == 1


def test_a_second_identical_call_adds_a_second_row(
    rw_write_database: Any, make_customer: Any, activity_rows: Any
) -> None:
    domain, _ = make_customer()
    first = _call(rw_write_database, customer_domain=domain)
    second = _call(rw_write_database, customer_domain=domain)
    assert first == second
    rows = activity_rows(domain)
    assert len(rows) == 2
    assert rows[0]["id"] != rows[1]["id"]
    ignored = ("id", "occurred_at")
    assert {k: v for k, v in rows[0].items() if k not in ignored} == {
        k: v for k, v in rows[1].items() if k not in ignored
    }


# 7. Die Datenbank als zweite Sperre (T; direkte INSERTs unter der Rolle, nicht über das Werkzeug).
# product_id, created_by 'seed'/'staff', die Typen quote/order/service sowie UPDATE, DELETE und TRUNCATE stehen schon in
# test_role_script_rw.py (test_role_cannot_insert_an_activity_with_*, test_role_cannot_update_delete_or_truncate).

DIRECT_INSERT = (
    "INSERT INTO activities (customer_id, contact_id, type, occurred_at, subject, summary, created_by, amount_eur) "
    "VALUES (%s, %s, %s, now(), 'Betreff', 'Zusammenfassung', %s, %s)"
)


def test_the_database_rejects_amount_eur_even_for_an_allowed_type(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    """amount_eur steht nicht in der Policy; der Tabellen-CHECK lehnt es ab (nur Angebot und Auftrag dürfen einen Betrag)."""
    domain, _ = make_customer()
    with pytest.raises(errors.CheckViolation):
        with rw_write_database.connection() as connection:
            connection.execute(
                DIRECT_INSERT, (_customer_id(admin, domain), None, "note", "agent", 100)
            )
    assert activity_rows(domain) == []


def test_the_database_rejects_created_by_human(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    """Policy (42501) oder CHECK (23514): 'human' ist kein Wert des Schemas; die Policy greift zuerst, beides lehnt ab."""
    domain, _ = make_customer()
    with pytest.raises((errors.InsufficientPrivilege, errors.CheckViolation)):
        with rw_write_database.connection() as connection:
            connection.execute(
                DIRECT_INSERT, (_customer_id(admin, domain), None, "note", "human", None)
            )
    assert activity_rows(domain) == []


def test_the_database_rejects_a_contact_of_another_customer(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    """Zweite Sperre neben der Prüfung im Werkzeug: der zusammengesetzte Fremdschlüssel (contact_id, customer_id)."""
    domain, _ = make_customer(contact=False)
    other_domain, other_email = make_customer()
    foreign_contact = _scalar(admin, "SELECT id FROM contacts WHERE email = %s", (other_email,))
    with pytest.raises(errors.ForeignKeyViolation):
        with rw_write_database.connection() as connection:
            connection.execute(
                DIRECT_INSERT, (_customer_id(admin, domain), foreign_contact, "note", "agent", None)
            )
    assert activity_rows(domain) == [] and activity_rows(other_domain) == []


def test_the_role_cannot_change_a_logged_row(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    domain, _ = make_customer()
    _call(rw_write_database, customer_domain=domain)
    before = activity_rows(domain)
    customer_id = _customer_id(admin, domain)
    with pytest.raises(errors.InsufficientPrivilege):
        with rw_write_database.connection() as connection:
            connection.execute(
                "UPDATE activities SET subject = 'geaendert' WHERE customer_id = %s", (customer_id,)
            )
    with pytest.raises(errors.InsufficientPrivilege):
        with rw_write_database.connection() as connection:
            connection.execute("DELETE FROM activities WHERE customer_id = %s", (customer_id,))
    assert activity_rows(domain) == before


# 8. Transaktion und Fehler


def test_a_failing_commit_stores_nothing(
    rw_write_database: Any,
    make_customer: Any,
    activity_rows: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.db import DATABASE_ERROR

    domain, _ = make_customer()

    def failing_commit(self: Any) -> None:
        raise psycopg.OperationalError("geheimtext")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "commit", failing_commit)
        error = _error(rw_write_database, customer_domain=domain)
    assert str(error) == DATABASE_ERROR
    assert "geheimtext" not in str(error)
    assert activity_rows(domain) == []


def test_a_failure_after_the_insert_rolls_the_insert_back(
    rw_write_database: Any, make_customer: Any, activity_rows: Any, admin: Any
) -> None:
    from hoffmann_data.db import DATABASE_ERROR

    domain, _ = make_customer()
    before = _snapshot(admin, domain)

    def explode_after_insert(text: str, params: Any) -> None:
        if text.lstrip().upper().startswith("INSERT"):
            raise RuntimeError("geheimtext")

    recorder = StatementRecorder(rw_write_database, on_execute=explode_after_insert)
    error = _error(recorder, customer_domain=domain)
    assert str(error) == DATABASE_ERROR
    assert activity_rows(domain) == []
    assert _snapshot(admin, domain) == before


@pytest.mark.parametrize(
    ("make_error", "class_name", "sqlstate"),
    [
        pytest.param(
            lambda: psycopg.OperationalError("geheimtext"),
            "OperationalError",
            None,
            id="verbindung",
        ),
        pytest.param(
            lambda: errors.InsufficientPrivilege("geheimtext"),
            "InsufficientPrivilege",
            "42501",
            id="recht",
        ),
        pytest.param(lambda: RuntimeError("geheimtext"), "RuntimeError", None, id="unerwartet"),
        pytest.param(
            lambda: errors.ForeignKeyViolation("geheimtext"),
            "ForeignKeyViolation",
            "23503",
            id="fremdschluessel",
        ),
    ],
)
def test_database_errors_become_the_fixed_message(
    caplog: pytest.LogCaptureFixture, make_error: Any, class_name: str, sqlstate: str | None
) -> None:
    from hoffmann_data.db import DATABASE_ERROR

    caplog.set_level(logging.DEBUG)
    error = _error(ExplodingDatabase(make_error()))
    assert str(error) == DATABASE_ERROR
    assert error.__cause__ is None
    records = _db_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert class_name in records[0].getMessage()
    if sqlstate is not None:
        assert sqlstate in records[0].getMessage()
    assert "geheimtext" not in _all_log_text(caplog)


def test_an_unreachable_database_gives_the_fixed_error(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unerreichbarer Port (Linux: OperationalError, Windows: ConnectionTimeout nach etwa 5 Sekunden)."""
    from hoffmann_data.db import DATABASE_ERROR, WriteDatabase

    caplog.set_level(logging.DEBUG)
    error = _error(WriteDatabase(UNREACHABLE_URL))
    assert str(error) == DATABASE_ERROR
    captured = capsys.readouterr()
    texts = (str(error), _all_log_text(caplog), captured.out, captured.err)
    for text in texts:
        for secret in DB_SECRETS:
            assert secret not in text, secret


def test_without_configuration_the_tool_says_not_configured() -> None:
    from hoffmann_data.db import NOT_CONFIGURED

    assert str(_error(None)) == NOT_CONFIGURED


# 9. Ende zu Ende über den Schreib-Pfad /mcp-write


def _rpc(
    client: Any,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any] | None = None,
    path: str = WRITE_PATH,
) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(path, json=payload, headers={**RPC_HEADERS, **headers})


def _tool_call(
    client: Any, headers: dict[str, str], arguments: dict[str, Any], path: str = WRITE_PATH
) -> Any:
    return _rpc(client, headers, "tools/call", {"name": TOOL, "arguments": arguments}, path)


def _result(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return parse_rpc_body(response)["result"]


def _arguments(domain: str, **extra: Any) -> dict[str, Any]:
    return {
        "customer_domain": domain,
        "type": "note",
        "subject": "Betreff",
        "summary": "Text",
        **extra,
    }


def test_the_tool_call_returns_the_structure_and_writes_the_row(
    e2e_client_for: Any,
    rw_write_database: Any,
    write_headers: dict[str, str],
    make_customer: Any,
    activity_rows: Any,
) -> None:
    domain, _ = make_customer()
    client = e2e_client_for(rw_write_database)
    result = _result(_tool_call(client, write_headers, _arguments(domain, type="complaint")))
    content = structured(result)
    assert content == {"customer_domain": domain, "type": "complaint", "logged": True}
    assert_no_internal_ids(content)
    assert [r["type"] for r in activity_rows(domain)] == ["complaint"]


def test_the_tool_call_with_a_contact(
    e2e_client_for: Any,
    rw_write_database: Any,
    write_headers: dict[str, str],
    make_customer: Any,
    activity_rows: Any,
    admin: Any,
) -> None:
    domain, email = make_customer()
    client = e2e_client_for(rw_write_database)
    structured(_result(_tool_call(client, write_headers, _arguments(domain, contact_email=email))))
    contact_id = _scalar(admin, "SELECT id FROM contacts WHERE email = %s", (email,))
    assert activity_rows(domain)[0]["contact_id"] == contact_id


def test_the_tool_call_rejects_an_invalid_type_without_touching_the_database(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    database = CountingDatabase()
    client = e2e_client_for(database)
    result = _result(_tool_call(client, write_headers, _arguments("firma.example", type="call")))
    assert result["isError"] is True
    assert _message("ACTIVITY_TYPE_INVALID") in error_text(result)
    assert database.calls == 0


def test_missing_required_arguments_are_an_error_without_database_access(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    """Der Fehler des SDKs für fehlende Pflichtargumente nennt den Argument-Dict (bekannte Ausnahme, Issue #21):
    geprüft wird nur, dass es ein Fehler ist und die Datenbank nicht berührt wird."""
    database = CountingDatabase()
    client = e2e_client_for(database)
    result = _result(_tool_call(client, write_headers, {}))
    assert result["isError"] is True
    assert not result.get("structuredContent")
    assert database.calls == 0


def test_the_read_token_on_the_write_path_stays_401(
    e2e_client_for: Any,
    rw_write_database: Any,
    read_headers: dict[str, str],
    make_customer: Any,
    activity_rows: Any,
) -> None:
    domain, _ = make_customer()
    client = e2e_client_for(rw_write_database)
    wrong = _tool_call(client, read_headers, _arguments(domain))
    missing = _tool_call(client, {}, _arguments(domain))
    assert wrong.status_code == missing.status_code == 401
    assert wrong.content == missing.content and dict(wrong.headers) == dict(missing.headers)
    assert activity_rows(domain) == []


def test_the_write_token_on_the_read_path_stays_401(
    e2e_client_for: Any,
    rw_write_database: Any,
    write_headers: dict[str, str],
    make_customer: Any,
    activity_rows: Any,
) -> None:
    domain, _ = make_customer()
    client = e2e_client_for(rw_write_database)
    assert _tool_call(client, write_headers, _arguments(domain), path=MCP_PATH).status_code == 401
    assert activity_rows(domain) == []


def test_log_activity_is_unknown_on_the_read_path(
    e2e_client_for: Any,
    rw_write_database: Any,
    read_headers: dict[str, str],
    make_customer: Any,
    activity_rows: Any,
) -> None:
    domain, _ = make_customer()
    client = e2e_client_for(rw_write_database)
    result = _result(_tool_call(client, read_headers, _arguments(domain), path=MCP_PATH))
    assert result["isError"] is True
    assert not result.get("structuredContent")
    assert activity_rows(domain) == []


def test_the_input_and_output_schema(e2e_client_for: Any, write_headers: dict[str, str]) -> None:
    database = CountingDatabase()
    client = e2e_client_for(database)
    tools = {t["name"]: t for t in _result(_rpc(client, write_headers, "tools/list"))["tools"]}
    tool = tools[TOOL]
    assert set(tool["inputSchema"]["properties"]) == {
        "customer_domain",
        "type",
        "subject",
        "summary",
        "contact_email",
    }
    assert set(tool["inputSchema"].get("required", [])) == {
        "customer_domain",
        "type",
        "subject",
        "summary",
    }
    output = tool.get("outputSchema")
    assert isinstance(output, dict) and set(output.get("properties", {})) == {
        "customer_domain",
        "type",
        "logged",
    }
    assert database.calls == 0


# 10. Kein Echo


def test_the_marker_in_subject_and_summary_is_never_echoed(
    e2e_client_for: Any,
    rw_write_database: Any,
    write_headers: dict[str, str],
    make_customer: Any,
    activity_rows: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    domain, _ = make_customer()
    caplog.set_level(logging.DEBUG)
    client = e2e_client_for(rw_write_database)
    arguments = _arguments(
        domain, subject=f"{SECRET_MARKER} Betreff", summary=f"Text {SECRET_MARKER}"
    )
    response = _tool_call(client, write_headers, arguments)
    result = _result(response)
    assert SECRET_MARKER not in json.dumps(result, ensure_ascii=False)
    assert [r["subject"] for r in activity_rows(domain)] == [f"{SECRET_MARKER} Betreff"], (
        "der Text ist gespeichert"
    )
    captured = capsys.readouterr()
    for text in (_all_log_text(caplog), captured.out, captured.err):
        assert SECRET_MARKER not in text


def test_the_success_path_writes_nothing_to_the_log(
    rw_write_database: Any, make_customer: Any, caplog: pytest.LogCaptureFixture
) -> None:
    domain, _ = make_customer()
    caplog.set_level(logging.DEBUG)
    _call(rw_write_database, customer_domain=domain)
    assert [r for r in caplog.records if r.name.startswith("hoffmann_data")] == []


def test_markers_in_rejected_inputs_never_reach_a_message_or_a_log(
    rw_write_database: Any,
    make_customer: Any,
    activity_rows: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    domain, _ = make_customer()
    caplog.set_level(logging.DEBUG)
    cases = [
        {"customer_domain": domain, "subject": SECRET_MARKER + "x" * 200},  # zu lang
        {"customer_domain": domain, "summary": SECRET_MARKER + NUL},  # NUL
        {"customer_domain": f"{MARKER_LOWER}.example"},  # gültige, unbekannte Domain
        {
            "customer_domain": domain,
            "contact_email": f"{MARKER_LOWER}@{domain}",
        },  # gültige, unbekannte Adresse
    ]
    for overrides in cases:
        error = _error(rw_write_database, **overrides)
        assert SECRET_MARKER.lower() not in str(error).lower()
    captured = capsys.readouterr()
    for text in (_all_log_text(caplog), captured.out, captured.err):
        assert SECRET_MARKER.lower() not in text.lower()
    assert [r for r in caplog.records if r.name.startswith("hoffmann_data")] == []
    assert activity_rows(domain) == []


# 11. Beschreibung


def test_the_description_is_no_placeholder_any_more(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    client = e2e_client_for(CountingDatabase())
    tools = {t["name"]: t for t in _result(_rpc(client, write_headers, "tools/list"))["tools"]}
    description = tools[TOOL]["description"]
    assert "platzhalter" not in description.lower()
    for word in (
        "inquiry",
        "complaint",
        "note",
        "customer_domain",
        "subject",
        "summary",
        "contact_email",
        "Domain",
    ):
        assert word in description, word
    assert re.search(r"nichts.*überschrieben.*gelöscht", description, re.IGNORECASE | re.DOTALL)


def test_the_write_server_instructions_no_longer_call_both_tools_placeholders(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    client = e2e_client_for(CountingDatabase())
    instructions = (
        _result(_rpc(client, write_headers, "initialize", INITIALIZE)).get("instructions") or ""
    ).lower()
    assert TOOL in instructions
    assert "beide werkzeuge sind noch platzhalter" not in instructions
