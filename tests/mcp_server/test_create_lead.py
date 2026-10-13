"""Tests des Werkzeugs create_lead (F08 Etappe 4c, ADR 0005 Punkte 5 bis 8): Lead anlegen, Dubletten verhindern.

Schnittstelle, die diese Tests festlegen:
- hoffmann_data.crm.create_lead(database, company_name, contact_first_name, contact_last_name, contact_email, language,
  country, contact_job_title=None, industry=None) -> {"customer_domain", "customer_created", "contact_created"}.
  Keine interne ID im Ergebnis. Status ist immer lead, die Domain des Kunden stammt aus der E-Mail-Adresse.
- Reihenfolge: Eingaben prüfen (Parameterreihenfolge), dann die Datenbank (write_connection): Kunde über die
  Domain, Kunde über den Firmennamen (ohne Beachtung der Schreibung), Konflikte, Eigentümer der Adresse, dann die
  INSERTs.
- Treffer über die Domain: der bestehende Kunde kommt unverändert zurück (kein Feld wird überschrieben, auch Status,
  Branche, Land, Sprache nicht); ein fehlender Kontakt wird angelegt, ein vorhandener bleibt unverändert.
- Konflikt (limits.LEAD_CONFLICT, nichts wird geschrieben): Der Firmenname gehört einem anderen Kunden als die Domain
  (oder, weil die Domain keinen Kunden trifft, überhaupt einem Kunden mit anderer Domain), oder die Adresse gehört schon
  einem anderen Kunden. Unterdomains werden nicht zusammengeführt (mail.firma.example ist ein anderer Kunde).
- created_at von Kunde und Kontakt setzt die Datenbank (DEFAULT now()), nie der Client.
- Eine Transaktion je Aufruf. Verliert ein Aufruf ein Rennen (UniqueViolation, db.DUPLICATE_ERROR), wiederholt crm den
  ganzen Ablauf genau einmal in einer neuen Transaktion; scheitert auch das, bleibt db.DUPLICATE_ERROR. Alle anderen
  Fehler der Datenbank (db.DATABASE_ERROR) werden nicht wiederholt.
- Es gibt keinen Schalter, der die Dublettenerkennung abschaltet.

Mit "T" markierte Tests brauchen die Test-Datenbank (TEST_DATABASE_URL, nur localhost) und laufen unter der echten Rolle
aus db/roles/data_service_rw.sql (Fixture rw_write_database), nicht als Eigentümer. Die Zeilen tragen den Marker aus
mcp_testkit; die Fixture env räumt vor und nach jedem Test auf. Mit "N" markierte brauchen keine Datenbank.
Rot, bis crm.create_lead, die Funktionen in db.py, limits.LEAD_CONFLICT und das Werkzeug existieren (TDD).
Was schon andere Dateien belegen, steht hier nicht doppelt: die Zeichenklassen der Eingaben (test_write_inputs.py),
Status != lead, UPDATE, DELETE, TRUNCATE der Rolle (test_role_script_rw.py).
"""

import inspect
import json
import logging
import re
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import (
    DB_SECRETS,
    MCP_PATH,
    READ_TOOLS,
    ROW_MARKER,
    SECRET_MARKER,
    CountingDatabase,
    ExplodingDatabase,
    StatementRecorder,
    assert_no_internal_ids,
    error_text,
    marker_row,
    result_of,
    rpc_post,
    structured,
    tool_call_post,
)
from psycopg import errors
from psycopg.rows import dict_row

TOOL = "create_lead"
DB_LOGGER = "hoffmann_data.db"
NUL = chr(0x00)
UNREACHABLE_URL = "postgresql://geheimuser:geheimpasswort@127.0.0.1:1/geheimdb"
MARKER_LIKE = f"{ROW_MARKER}-%"
THREADS = 8
ROUNDS = 5
INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"},
}
# Die acht Parameter in der Reihenfolge des Werkzeugs (und der Prüfung der Eingaben)
PARAMETERS = (
    "company_name",
    "contact_first_name",
    "contact_last_name",
    "contact_email",
    "language",
    "country",
    "contact_job_title",
    "industry",
)
REQUIRED = PARAMETERS[:6]
OUTPUT_KEYS = {"customer_domain", "customer_created", "contact_created"}


@dataclass
class Env:
    """Die Rolle (Schreibweg) und die Verbindung des Eigentümers (zum Anlegen, Zählen und Aufräumen)."""

    role: Any
    admin: Any


@pytest.fixture
def env(rw_write_database: Any, admin: Any, marker_cleanup: None) -> Env:
    return Env(rw_write_database, admin)


def _limits() -> Any:
    from hoffmann_data import limits

    return limits


def _message(name: str) -> str:
    return str(getattr(_limits(), name))


def _args(row: dict[str, str], **overrides: Any) -> dict[str, Any]:
    """Alle acht Argumente mit gültigen Werten aus einer Marker-Zeile; `overrides` ersetzt einzelne."""
    values: dict[str, Any] = {
        "company_name": row["company_name"],
        "contact_first_name": "Erika",
        "contact_last_name": "Muster",
        "contact_email": row["email"],
        "language": "de",
        "country": "de",
        "contact_job_title": None,
        "industry": None,
    }
    unknown = set(overrides) - set(values)
    assert not unknown, unknown
    return {**values, **overrides}


def _call(database: Any, args: dict[str, Any]) -> Any:
    from hoffmann_data import crm

    return crm.create_lead(database, *(args[name] for name in PARAMETERS))


def _error(database: Any, args: dict[str, Any]) -> ToolError:
    """Der ToolError des Aufrufs; bricht ab, wenn der Aufruf gelingt."""
    with pytest.raises(ToolError) as info:
        _call(database, args)
    return info.value


def _result(domain: str, customer_created: bool, contact_created: bool) -> dict[str, Any]:
    return {
        "customer_domain": domain,
        "customer_created": customer_created,
        "contact_created": contact_created,
    }


def _rows(admin: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with admin.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def _customers(admin: Any, domain: str) -> list[dict[str, Any]]:
    return _rows(admin, "SELECT * FROM customers WHERE domain = %s ORDER BY id", (domain,))


def _contacts(admin: Any, email: str) -> list[dict[str, Any]]:
    return _rows(admin, "SELECT * FROM contacts WHERE email = %s ORDER BY id", (email,))


def _state(admin: Any) -> dict[str, list[dict[str, Any]]]:
    """Alle Marker-Zeilen von customers und contacts, vollständig (zum Vergleich vor und nach dem Aufruf)."""
    return {
        "customers": _rows(
            admin, "SELECT * FROM customers WHERE domain LIKE %s ORDER BY id", (MARKER_LIKE,)
        ),
        "contacts": _rows(
            admin,
            "SELECT * FROM contacts WHERE customer_id IN (SELECT id FROM customers WHERE domain LIKE %s) "
            "ORDER BY id",
            (MARKER_LIKE,),
        ),
    }


def _add_customer(
    admin: Any,
    company_name: str,
    domain: str,
    *,
    status: str = "existing",
    industry: str = "other",
    country: str = "de",
) -> int:
    found = admin.execute(
        "INSERT INTO customers (company_name, domain, industry, country, status) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (company_name, domain, industry, country, status),
    ).fetchone()
    assert found is not None
    return int(found[0])


def _add_contact(
    admin: Any,
    customer_id: int,
    email: str,
    *,
    first_name: str = "Alt",
    last_name: str = "Bestand",
    job_title: str | None = None,
    language: str = "en",
) -> None:
    admin.execute(
        "INSERT INTO contacts (customer_id, first_name, last_name, email, job_title, language) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (customer_id, first_name, last_name, email, job_title, language),
    )


def _existing(admin: Any, row: dict[str, str], *, contact: bool = True, **fields: Any) -> int:
    """Legt einen Kunden mit den Werten der Marker-Zeile an, auf Wunsch mit dem Kontakt der Zeile."""
    customer_id = _add_customer(admin, row["company_name"], row["domain"], **fields)
    if contact:
        _add_contact(admin, customer_id, row["email"], job_title="Leitung")
    return customer_id


def _child_domain(domain: str) -> str:
    """Eine Unterdomain, die mit dem Marker beginnt (nur dann räumt delete_marker_rows sie auf)."""
    return f"{ROW_MARKER}-mail.{domain}"


def _db_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == DB_LOGGER]


def _package_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("hoffmann_data")]


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)


def _head(text: str) -> str:
    return text.lstrip().upper()


# 1. Abnahmekriterium F08 (T)


def test_a_second_create_lead_with_the_same_data_creates_no_duplicate_customer(env: Env) -> None:
    """Abnahmekriterium F08: Ein zweiter create_lead mit denselben Daten legt keinen zweiten Kunden an."""
    row = marker_row()
    args = _args(row)
    first = _call(env.role, args)
    assert first == _result(row["domain"], True, True)
    assert len(_customers(env.admin, row["domain"])) == 1
    assert len(_contacts(env.admin, row["email"])) == 1
    second = _call(env.role, args)
    assert second == _result(row["domain"], False, False), "die Rückgabe sagt: nicht neu angelegt"
    assert len(_customers(env.admin, row["domain"])) == 1, "kein zweiter Kunde"
    assert len(_contacts(env.admin, row["email"])) == 1, "kein zweiter Kontakt"
    assert len(_state(env.admin)["customers"]) == 1


def test_the_duplicate_is_found_despite_other_spelling_and_edge_whitespace(env: Env) -> None:
    row = marker_row()
    _call(env.role, _args(row))
    again = _args(
        row,
        company_name="  " + row["company_name"].upper() + "  ",
        contact_email=row["email"].upper(),
    )
    assert _call(env.role, again) == _result(row["domain"], False, False)
    assert len(_state(env.admin)["customers"]) == 1
    assert len(_state(env.admin)["contacts"]) == 1


# 2. Zuordnung (T)


def test_a_match_by_domain_ignores_a_different_company_name_and_keeps_the_customer(
    env: Env,
) -> None:
    row = marker_row()
    _existing(env.admin, row)
    before = _state(env.admin)
    args = _args(
        row,
        company_name=f"Ganz anderer Name {uuid.uuid4().hex[:12]}",
        contact_email=f"neu@{row['domain'].upper()}",
    )
    result = _call(env.role, args)
    assert result == _result(row["domain"], False, True)
    after = _state(env.admin)
    assert after["customers"] == before["customers"]
    assert after["contacts"][: len(before["contacts"])] == before["contacts"]


def test_domain_and_company_name_of_the_same_customer_are_a_match(env: Env) -> None:
    row = marker_row()
    _existing(env.admin, row)
    args = _args(
        row,
        company_name="   " + row["company_name"].upper() + "   ",
        contact_email=f"zweite@{row['domain']}",
    )
    assert _call(env.role, args) == _result(row["domain"], False, True)
    assert len(_state(env.admin)["customers"]) == 1


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(lambda name: name, id="gleich"),
        pytest.param(lambda name: name.upper(), id="gross"),
        pytest.param(lambda name: name.capitalize(), id="erster-grossbuchstabe"),
        pytest.param(lambda name: "  " + name + "  ", id="randleerraum"),
    ],
)
def test_a_match_by_company_name_alone_is_a_conflict(
    env: Env, variant: Callable[[str], str]
) -> None:
    """Die Domain der Adresse trifft keinen Kunden, der Firmenname aber Kunde B (andere Domain): Konflikt."""
    known = marker_row()
    _existing(env.admin, known)
    before = _state(env.admin)
    new = marker_row()
    error = _error(env.role, _args(new, company_name=variant(known["company_name"])))
    assert str(error) == _message("LEAD_CONFLICT")
    assert _state(env.admin) == before, "es wurde nichts geschrieben"
    assert _customers(env.admin, new["domain"]) == []


def test_the_domain_goes_before_the_company_name(env: Env) -> None:
    """Domain trifft Kunde A, der Name Kunde B: Konflikt (es wird nicht geraten). Trifft der Name niemanden: A."""
    a, b = marker_row(), marker_row()
    _existing(env.admin, a)
    _existing(env.admin, b)
    before = _state(env.admin)
    error = _error(env.role, _args(a, company_name=b["company_name"]))
    assert str(error) == _message("LEAD_CONFLICT")
    assert _state(env.admin) == before
    unnamed = _args(a, company_name=f"Niemandes Name {uuid.uuid4().hex[:12]}")
    assert _call(env.role, unnamed) == _result(a["domain"], False, False)


def test_a_subdomain_is_a_new_customer_with_another_company_name(env: Env) -> None:
    parent = marker_row()
    _existing(env.admin, parent)
    before = _state(env.admin)
    child = _child_domain(parent["domain"])
    row = {
        "company_name": f"{ROW_MARKER} Tochter {uuid.uuid4().hex[:12]}",
        "domain": child,
        "email": f"kontakt@{child}",
    }
    assert _call(env.role, _args(row)) == _result(child, True, True)
    after = _state(env.admin)
    assert len(after["customers"]) == 2
    assert [c for c in after["customers"] if c["domain"] == parent["domain"]] == before["customers"]


def test_a_subdomain_with_the_same_company_name_is_a_conflict(env: Env) -> None:
    parent = marker_row()
    _existing(env.admin, parent)
    before = _state(env.admin)
    child = _child_domain(parent["domain"])
    args = _args(
        {"company_name": parent["company_name"], "domain": child, "email": f"kontakt@{child}"}
    )
    assert str(_error(env.role, args)) == _message("LEAD_CONFLICT")
    assert _state(env.admin) == before


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(lambda name: name + " GmbH", id="zusatz-gmbh"),
        pytest.param(lambda name: name[:-1], id="praefix"),
        pytest.param(lambda name: "Neue " + name, id="wort-davor"),
    ],
)
def test_a_similar_company_name_is_no_match(env: Env, variant: Callable[[str], str]) -> None:
    known = marker_row()
    _existing(env.admin, known)
    before = _state(env.admin)
    new = marker_row()
    result = _call(env.role, _args(new, company_name=variant(known["company_name"])))
    assert result == _result(new["domain"], True, True)
    after = _state(env.admin)
    assert len(after["customers"]) == 2
    assert [c for c in after["customers"] if c["domain"] == known["domain"]] == before["customers"]


# 3. Bestehende Daten bleiben unverändert (T)


@pytest.mark.parametrize("status", ["existing", "lead", "inactive"])
def test_an_existing_customer_and_contact_stay_unchanged_field_by_field(
    env: Env, status: str
) -> None:
    row = marker_row()
    customer_id = _add_customer(
        env.admin, row["company_name"], row["domain"], status=status, industry="food", country="at"
    )
    _add_contact(
        env.admin,
        customer_id,
        row["email"],
        first_name="Hans",
        last_name="Alt",
        job_title="Leiter",
        language="en",
    )
    before = _state(env.admin)
    args = _args(
        row,
        company_name=f"Anderer Name {uuid.uuid4().hex[:12]}",
        contact_first_name="Neu",
        contact_last_name="Name",
        language="de",
        country="ch",
        contact_job_title="Anderes",
        industry="metalworking",
    )
    assert _call(env.role, args) == _result(row["domain"], False, False)
    assert _state(env.admin) == before, (
        "kein Feld wurde überschrieben (auch id und created_at gleich)"
    )


def test_a_new_contact_is_added_to_an_existing_customer(env: Env) -> None:
    row = marker_row()
    _existing(env.admin, row, status="inactive", industry="food", country="at")
    before = _state(env.admin)
    args = _args(
        row,
        contact_first_name=" Paula ",
        contact_last_name="Neu",
        contact_email=f"Paula@{row['domain']}",
        language="en",
        contact_job_title="Einkauf",
    )
    assert _call(env.role, args) == _result(row["domain"], False, True)
    after = _state(env.admin)
    assert after["customers"] == before["customers"]
    assert after["contacts"][: len(before["contacts"])] == before["contacts"]
    added = [c for c in after["contacts"] if c["email"] == f"paula@{row['domain']}"]
    assert len(added) == 1
    assert (
        added[0]["first_name"],
        added[0]["last_name"],
        added[0]["language"],
        added[0]["job_title"],
    ) == (
        "Paula",
        "Neu",
        "en",
        "Einkauf",
    )
    assert added[0]["customer_id"] == before["customers"][0]["id"]


def test_the_same_contact_twice_is_one_row(env: Env) -> None:
    row = marker_row()
    _existing(env.admin, row, contact=False)
    args = _args(row)
    assert _call(env.role, args) == _result(row["domain"], False, True)
    assert _call(env.role, args) == _result(row["domain"], False, False)
    assert len(_contacts(env.admin, row["email"])) == 1


# 4. Neuer Kunde (T)


def test_a_new_customer_and_contact_are_written_with_the_given_values(env: Env) -> None:
    row = marker_row()
    args = _args(
        row,
        company_name="  " + row["company_name"] + "  ",
        contact_first_name=" Erika ",
        contact_last_name="Muster",
        contact_email=row["email"].upper(),
        language="en",
        country="at",
        contact_job_title="  Einkauf  ",
        industry="food",
    )
    assert _call(env.role, args) == _result(row["domain"], True, True)
    customers = _customers(env.admin, row["domain"])
    assert len(customers) == 1
    customer = customers[0]
    assert customer["company_name"] == row["company_name"]
    assert (customer["status"], customer["industry"], customer["country"]) == ("lead", "food", "at")
    contacts = _contacts(env.admin, row["email"])
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["customer_id"] == customer["id"]
    assert (contact["first_name"], contact["last_name"], contact["language"]) == (
        "Erika",
        "Muster",
        "en",
    )
    assert contact["job_title"] == "Einkauf"
    assert contact["email"] == row["email"]


def test_the_defaults_are_industry_other_and_no_job_title(env: Env) -> None:
    row = marker_row()
    _call(env.role, _args(row))
    assert _customers(env.admin, row["domain"])[0]["industry"] == "other"
    assert _contacts(env.admin, row["email"])[0]["job_title"] is None


@pytest.mark.parametrize("empty", [None, "", "   "], ids=repr)
def test_an_empty_job_title_is_stored_as_null(env: Env, empty: str | None) -> None:
    row = marker_row()
    _call(env.role, _args(row, contact_job_title=empty))
    assert _contacts(env.admin, row["email"])[0]["job_title"] is None


def test_the_timestamps_come_from_the_database(env: Env, db_clock: Any) -> None:
    row = marker_row()
    before = db_clock()
    _call(env.role, _args(row))
    after = db_clock()
    customer = _customers(env.admin, row["domain"])[0]
    contact = _contacts(env.admin, row["email"])[0]
    assert before <= customer["created_at"] <= after
    assert before <= contact["created_at"] <= after
    assert contact["created_at"] >= customer["created_at"], (
        "Prüfung 3b: nie ein Kontakt vor seiner Firma"
    )


def test_the_inserts_send_no_client_time_and_no_status(env: Env) -> None:
    """created_at von Kunde und Kontakt kommt aus dem DEFAULT der Datenbank: Keiner der beiden INSERTs nennt die Spalte,
    und keiner schickt einen Zeitwert; der Status steht fest als 'lead' im Text.
    (occurred_at der Aktivität: test_log_activity.py)"""
    row = marker_row()
    recorder = StatementRecorder(env.role)
    _call(recorder, _args(row))
    inserts = [s for s in recorder.statements if _head(s[0]).startswith("INSERT")]
    assert len(inserts) == 2, "ein Kunde und ein Kontakt"
    for text, params in inserts:
        values = list(params.values()) if isinstance(params, dict) else list(params or ())
        assert not [v for v in values if isinstance(v, date | float)], values
        assert "created_at" not in text.lower(), (
            "created_at setzt die Datenbank (DEFAULT now()), nie der Aufruf"
        )
        assert "lead" not in values, "der Status ist nicht vom Aufrufer änderbar"
    customer_insert = next(
        text for text, _ in inserts if _head(text).startswith("INSERT INTO CUSTOMERS")
    )
    assert "'lead'" in customer_insert


# 5. Konflikte (T)


def _conflict_cases(env: Env) -> list[tuple[str, dict[str, Any]]]:
    """Vier Konflikte als (Name, Argumente); die Zeilen dafür stehen schon in der Datenbank."""
    a, b = marker_row(), marker_row()
    _existing(env.admin, a)
    _existing(env.admin, b)
    name_only = marker_row()
    foreign_known = marker_row()
    _existing(env.admin, foreign_known, contact=False)
    holder = marker_row()
    holder_id = _existing(env.admin, holder, contact=False)
    _add_contact(env.admin, holder_id, f"fremd@{foreign_known['domain']}")
    new_domain = marker_row()
    _add_contact(env.admin, holder_id, f"fremd@{new_domain['domain']}")
    return [
        ("domain-a-name-b", _args(a, company_name=b["company_name"])),
        ("nur-name", _args(name_only, company_name=a["company_name"])),
        (
            "adresse-fremd-kunde-bestehend",
            _args(foreign_known, contact_email=f"fremd@{foreign_known['domain']}"),
        ),
        (
            "adresse-fremd-kunde-neu",
            _args(new_domain, contact_email=f"fremd@{new_domain['domain']}"),
        ),
    ]


@pytest.mark.parametrize("index", range(4))
def test_a_conflict_gives_the_fixed_message_and_writes_nothing(env: Env, index: int) -> None:
    case_name, args = _conflict_cases(env)[index]
    before = _state(env.admin)
    error = _error(env.role, args)
    assert str(error) == _message("LEAD_CONFLICT"), case_name
    assert error.__cause__ is None
    for secret in (
        args["company_name"],
        args["contact_email"],
        args["contact_email"].partition("@")[2],
        ROW_MARKER,
    ):
        assert secret.lower() not in str(error).lower(), "die Meldung nennt keine Eingabe"
    assert _state(env.admin) == before, (
        f"{case_name}: nichts wurde geschrieben, auch kein neuer Kunde"
    )


# 5b. Die festen Meldungen sind paarweise verschieden (N)


def test_the_fixed_messages_of_the_write_tools_are_pairwise_distinct() -> None:
    from hoffmann_data import db, limits

    messages: dict[str, str] = {
        "limits.LEAD_CONFLICT": limits.LEAD_CONFLICT,
        "db.DUPLICATE_ERROR": db.DUPLICATE_ERROR,
        "db.DATABASE_ERROR": db.DATABASE_ERROR,
        "db.NOT_CONFIGURED": db.NOT_CONFIGURED,
        "limits.CUSTOMER_NOT_FOUND": limits.CUSTOMER_NOT_FOUND,
        "limits.CONTACT_NOT_FOUND": limits.CONTACT_NOT_FOUND,
    }
    for name, value in vars(limits).items():
        if name.endswith("_INVALID") and isinstance(value, str):
            messages[f"limits.{name}"] = value
    assert len(messages) >= 6 + 12, "die _INVALID-Meldungen der Schreib-Werkzeuge fehlen"
    assert all(isinstance(v, str) and v.strip() for v in messages.values())
    values = list(messages.values())
    assert len(set(values)) == len(values), "zwei Meldungen sind gleich"
    for name, value in messages.items():
        for other_name, other in messages.items():
            if name != other_name:
                assert value not in other, f"{name} steckt in {other_name}"
    for secret in (ROW_MARKER, "@"):
        assert secret not in limits.LEAD_CONFLICT and secret not in db.DUPLICATE_ERROR


# 6. Zweite Sperre unter der Rolle (T): direkte INSERTs, nicht über das Werkzeug

CUSTOMER_INSERT = (
    "INSERT INTO customers (company_name, domain, industry, country, status) "
    "VALUES (%s, %s, 'other', 'de', 'lead')"
)
CONTACT_INSERT = (
    "INSERT INTO contacts (customer_id, first_name, last_name, email, language) "
    "VALUES (%s, 'Erika', 'Muster', %s, 'de')"
)


def _constraint(info: Any) -> str | None:
    return info.value.diag.constraint_name


def test_the_database_rejects_a_duplicate_domain(env: Env) -> None:
    row = marker_row()
    _existing(env.admin, row)
    before = _state(env.admin)
    with pytest.raises(errors.UniqueViolation) as info:
        with env.role.connection() as connection:
            connection.execute(
                CUSTOMER_INSERT, (f"{ROW_MARKER} anders {uuid.uuid4().hex[:12]}", row["domain"])
            )
    assert _constraint(info) == "customers_domain_key"
    assert _state(env.admin) == before


def test_the_database_rejects_a_duplicate_company_name_in_another_spelling(env: Env) -> None:
    row = marker_row()
    _existing(env.admin, row)
    before = _state(env.admin)
    with pytest.raises(errors.UniqueViolation) as info:
        with env.role.connection() as connection:
            connection.execute(
                CUSTOMER_INSERT, (row["company_name"].upper(), marker_row()["domain"])
            )
    assert _constraint(info) == "customers_company_name_lower_key"
    assert _state(env.admin) == before


def test_the_database_rejects_a_duplicate_email(env: Env) -> None:
    row = marker_row()
    customer_id = _existing(env.admin, row)
    before = _state(env.admin)
    with pytest.raises(errors.UniqueViolation) as info:
        with env.role.connection() as connection:
            connection.execute(CONTACT_INSERT, (customer_id, row["email"]))
    assert _constraint(info) == "contacts_email_key"
    assert _state(env.admin) == before


# 7. Gleichzeitige Aufrufe und verlorene Rennen (T)


def _run_threads(database: Any, argument_sets: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    """Startet alle Aufrufe gleichzeitig (Barrier), je mit eigener Verbindung. Ergebnis je Aufruf: ("ok", dict) oder
    ("fehler", Meldung)."""
    from hoffmann_data import crm  # noqa: F401 - vor den Threads importieren, nicht darin

    barrier = threading.Barrier(len(argument_sets))

    def worker(args: dict[str, Any]) -> tuple[str, Any]:
        barrier.wait(timeout=30)
        try:
            return ("ok", _call(database, args))
        except ToolError as error:
            return ("fehler", str(error))

    with ThreadPoolExecutor(max_workers=len(argument_sets)) as pool:
        futures = [pool.submit(worker, args) for args in argument_sets]
        return [future.result(timeout=60) for future in futures]


def _all_ok(results: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """Alle Antworten sind Dicts: weder DATABASE_ERROR noch DUPLICATE_ERROR kommt beim Aufrufer an."""
    errors_seen = [value for kind, value in results if kind != "ok"]
    assert not errors_seen, f"Fehler statt Ergebnis: {errors_seen}"
    return [value for _, value in results]


def _assert_only_race_warnings(
    caplog: pytest.LogCaptureFixture, row: dict[str, str], at_most: int
) -> None:
    """Verlierer eines Rennens loggen je Fehlversuch eine WARNING mit Klasse und SQLSTATE, nie mit Werten."""
    records = _db_records(caplog)
    assert len(records) <= at_most
    for record in records:
        assert record.levelno == logging.WARNING
        assert "UniqueViolation" in record.getMessage()
    text = _all_log_text(caplog)
    for secret in (row["company_name"], row["domain"], row["email"]):
        assert secret not in text


def test_eight_concurrent_calls_for_the_same_new_lead_create_one_customer_and_one_contact(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for _ in range(ROUNDS):
        caplog.clear()
        row = marker_row()
        results = _all_ok(_run_threads(env.role, [_args(row)] * THREADS))
        assert len(_customers(env.admin, row["domain"])) == 1
        assert len(_contacts(env.admin, row["email"])) == 1
        assert sum(r["customer_created"] for r in results) == 1
        assert sum(r["contact_created"] for r in results) == 1
        assert results.count(_result(row["domain"], True, True)) == 1
        assert results.count(_result(row["domain"], False, False)) == THREADS - 1
        _assert_only_race_warnings(caplog, row, THREADS - 1)


def test_eight_concurrent_calls_add_the_same_new_contact_to_an_existing_customer_once(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for _ in range(ROUNDS):
        caplog.clear()
        row = marker_row()
        _existing(env.admin, row, contact=False)
        before = _customers(env.admin, row["domain"])
        results = _all_ok(_run_threads(env.role, [_args(row)] * THREADS))
        assert _customers(env.admin, row["domain"]) == before, "der Kunde bleibt unverändert"
        assert len(_contacts(env.admin, row["email"])) == 1
        assert not any(r["customer_created"] for r in results)
        assert sum(r["contact_created"] for r in results) == 1
        _assert_only_race_warnings(caplog, row, THREADS - 1)


def test_eight_concurrent_calls_with_different_contacts_create_one_customer_and_eight_contacts(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for _ in range(ROUNDS):
        caplog.clear()
        row = marker_row()
        emails = [f"person{i}@{row['domain']}" for i in range(THREADS)]
        results = _all_ok(
            _run_threads(env.role, [_args(row, contact_email=email) for email in emails])
        )
        customers = _customers(env.admin, row["domain"])
        assert len(customers) == 1
        contacts = _rows(
            env.admin, "SELECT * FROM contacts WHERE customer_id = %s", (customers[0]["id"],)
        )
        assert sorted(c["email"] for c in contacts) == sorted(emails)
        assert sum(r["customer_created"] for r in results) == 1
        assert all(r["contact_created"] for r in results)
        _assert_only_race_warnings(caplog, row, THREADS - 1)


def _competitor_hook(
    admin: Any, row: dict[str, str], *, with_contact: bool
) -> tuple[dict[str, Any], Callable[..., None]]:
    """Hook für StatementRecorder(before_execute=...): vor dem ersten INSERT INTO customers des Aufrufs legt ein
    Konkurrent über die Verbindung des Eigentümers (autocommit, also schon committet) denselben Kunden an.
    Der INSERT des Aufrufs scheitert dann mit dem echten SQLSTATE 23505 der Datenbank."""
    state: dict[str, Any] = {"fired": False, "seen": []}

    def hook(text: str, params: Any) -> None:
        head = _head(text)
        state["seen"].append(head)
        if head.startswith("INSERT INTO CUSTOMERS") and not state["fired"]:
            state["fired"] = True
            admin.execute(
                "INSERT INTO customers (company_name, domain, industry, country, status) "
                "VALUES (%s, %s, 'food', 'at', 'lead')",
                (row["company_name"], row["domain"]),
            )
            if with_contact:
                admin.execute(
                    "INSERT INTO contacts (customer_id, first_name, last_name, email, language) "
                    "SELECT id, 'Konkurrent', 'Test', %s::text, 'en' FROM customers WHERE domain = %s::text",
                    (row["email"], row["domain"]),
                )

    return state, hook


def _seen(state: dict[str, Any], prefix: str) -> list[str]:
    return [head for head in state["seen"] if head.startswith(prefix)]


def test_a_lost_race_is_retried_once_and_returns_the_existing_customer(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    row = marker_row()
    state, hook = _competitor_hook(env.admin, row, with_contact=True)
    recorder = StatementRecorder(env.role, before_execute=hook)
    result = _call(recorder, _args(row, industry="metalworking", country="ch"))
    assert result == _result(row["domain"], False, False)
    assert recorder.connections == 2, "ein Versuch und genau eine Wiederholung"
    assert len(_seen(state, "INSERT INTO CUSTOMERS")) == 1, (
        "nur der verlorene INSERT, die Wiederholung schreibt nichts"
    )
    assert _seen(state, "INSERT INTO CONTACTS") == []
    customers = _customers(env.admin, row["domain"])
    assert len(customers) == 1
    assert (customers[0]["industry"], customers[0]["country"]) == ("food", "at"), (
        "die Zeile des Konkurrenten"
    )
    assert len(_contacts(env.admin, row["email"])) == 1
    records = _db_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert "UniqueViolation" in records[0].getMessage() and "23505" in records[0].getMessage()
    _assert_only_race_warnings(caplog, row, 1)


def test_a_lost_race_is_retried_as_a_whole_and_adds_the_missing_contact(env: Env) -> None:
    """Der Konkurrent legte nur den Kunden an: Die Wiederholung läuft den ganzen Ablauf neu und ergänzt den Kontakt."""
    row = marker_row()
    state, hook = _competitor_hook(env.admin, row, with_contact=False)
    recorder = StatementRecorder(env.role, before_execute=hook)
    assert _call(recorder, _args(row)) == _result(row["domain"], False, True)
    assert recorder.connections == 2
    assert len(_seen(state, "INSERT INTO CUSTOMERS")) == 1
    assert len(_seen(state, "INSERT INTO CONTACTS")) == 1
    customers = _customers(env.admin, row["domain"])
    assert len(customers) == 1 and (customers[0]["industry"], customers[0]["country"]) == (
        "food",
        "at",
    )
    contacts = _contacts(env.admin, row["email"])
    assert len(contacts) == 1 and contacts[0]["first_name"] == "Erika"
    assert contacts[0]["customer_id"] == customers[0]["id"]


@pytest.mark.parametrize("prefix", ["INSERT INTO CUSTOMERS", "INSERT INTO CONTACTS"])
def test_a_second_lost_race_gives_the_fixed_duplicate_message(
    env: Env, prefix: str, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ohne Konkurrenten kann die Wiederholung nicht gewinnen: genau ein Versuch mehr, dann die feste Meldung."""
    from hoffmann_data.db import DUPLICATE_ERROR

    caplog.set_level(logging.DEBUG)
    row = marker_row()
    calls: list[str] = []

    def always_duplicate(text: str, params: Any) -> None:
        if _head(text).startswith(prefix):
            calls.append(text)
            raise errors.UniqueViolation("geheimtext")

    recorder = StatementRecorder(env.role, before_execute=always_duplicate)
    error = _error(recorder, _args(row))
    assert str(error) == DUPLICATE_ERROR
    assert error.__cause__ is None
    assert len(calls) == 2 and recorder.connections == 2, (
        "genau eine Wiederholung, nie eine Schleife"
    )
    assert _state(env.admin) == {"customers": [], "contacts": []}, (
        "auch der Kunde des Versuchs ist zurückgerollt"
    )
    records = _db_records(caplog)
    assert len(records) == 2, "je Fehlversuch eine WARNING"
    assert all("UniqueViolation" in r.getMessage() for r in records)
    captured = capsys.readouterr()
    for text in (str(error), _all_log_text(caplog), captured.out, captured.err):
        assert "geheimtext" not in text


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
        pytest.param(
            lambda: errors.ForeignKeyViolation("geheimtext"),
            "ForeignKeyViolation",
            "23503",
            id="fremdschluessel",
        ),
        pytest.param(lambda: RuntimeError("geheimtext"), "RuntimeError", None, id="unerwartet"),
    ],
)
def test_other_database_errors_are_not_retried(
    make_error: Callable[[], BaseException],
    class_name: str,
    sqlstate: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from hoffmann_data.db import DATABASE_ERROR

    caplog.set_level(logging.DEBUG)
    recorder = StatementRecorder(ExplodingDatabase(make_error()))
    error = _error(recorder, _args(marker_row()))
    assert str(error) == DATABASE_ERROR
    assert error.__cause__ is None
    assert recorder.connections == 1, "nur DUPLICATE_ERROR wird wiederholt"
    records = _db_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert class_name in records[0].getMessage()
    if sqlstate is not None:
        assert sqlstate in records[0].getMessage()
    assert "geheimtext" not in _all_log_text(caplog)


# 8. Eingabeprüfung vor jedem Datenbankzugriff (N)
# Die Zeichenklassen und Längen je Feld prüft test_write_inputs.py; hier geht es darum, dass create_lead sie in dieser
# Reihenfolge anwendet und die Datenbank nie berührt.

INVALID_VALUES: list[Any] = [
    pytest.param("company_name", "", "COMPANY_NAME_INVALID", id="firma-leer"),
    pytest.param("company_name", "x" * 201, "COMPANY_NAME_INVALID", id="firma-201"),
    pytest.param("company_name", "a" + NUL, "COMPANY_NAME_INVALID", id="firma-nul"),
    pytest.param("company_name", None, "COMPANY_NAME_INVALID", id="firma-none"),
    pytest.param("contact_first_name", "", "FIRST_NAME_INVALID", id="vorname-leer"),
    pytest.param("contact_first_name", "x" * 101, "FIRST_NAME_INVALID", id="vorname-101"),
    pytest.param("contact_last_name", "", "LAST_NAME_INVALID", id="nachname-leer"),
    pytest.param("contact_last_name", "x" * 101, "LAST_NAME_INVALID", id="nachname-101"),
    pytest.param("contact_email", "kein-at", "EMAIL_INVALID", id="adresse-ohne-at"),
    pytest.param("contact_email", "a" * 241 + "@firma.example", "EMAIL_INVALID", id="adresse-255"),
    pytest.param("contact_email", "x@firma.de", "EMAIL_INVALID", id="adresse-nicht-example"),
    pytest.param("contact_email", 5, "EMAIL_INVALID", id="adresse-zahl"),
    pytest.param(
        "contact_email",
        "m" + chr(0xFC) + "ller@firma.example",
        "EMAIL_INVALID",
        id="adresse-umlaut-lokalteil",
    ),
    pytest.param(
        "contact_email",
        chr(0x212A) + "ontakt@firma.example",
        "EMAIL_INVALID",
        id="adresse-kelvin-lokalteil",
    ),
    pytest.param("language", "fr", "LANGUAGE_INVALID", id="sprache-fr"),
    pytest.param("language", "DE", "LANGUAGE_INVALID", id="sprache-gross"),
    pytest.param("language", None, "LANGUAGE_INVALID", id="sprache-none"),
    pytest.param("language", 5, "LANGUAGE_INVALID", id="sprache-zahl"),
    pytest.param("country", None, "COUNTRY_INVALID", id="land-fehlt"),
    pytest.param("country", "us", "COUNTRY_INVALID", id="land-us"),
    pytest.param("country", "DE", "COUNTRY_INVALID", id="land-gross"),
    pytest.param("country", "", "COUNTRY_INVALID", id="land-leer"),
    pytest.param("contact_job_title", "x" * 101, "JOB_TITLE_INVALID", id="position-101"),
    pytest.param("contact_job_title", "a" + NUL, "JOB_TITLE_INVALID", id="position-nul"),
    pytest.param("contact_job_title", ["a"], "JOB_TITLE_INVALID", id="position-liste"),
    pytest.param("industry", "steel", "INDUSTRY_INVALID", id="branche-steel"),
    pytest.param("industry", "OTHER", "INDUSTRY_INVALID", id="branche-gross"),
    pytest.param("industry", "", "INDUSTRY_INVALID", id="branche-leer"),
    pytest.param("industry", 5, "INDUSTRY_INVALID", id="branche-zahl"),
]
FIELD_MESSAGES = (
    ("company_name", "COMPANY_NAME_INVALID", ""),
    ("contact_first_name", "FIRST_NAME_INVALID", ""),
    ("contact_last_name", "LAST_NAME_INVALID", ""),
    ("contact_email", "EMAIL_INVALID", "kein-at"),
    ("language", "LANGUAGE_INVALID", "fr"),
    ("country", "COUNTRY_INVALID", None),
    ("contact_job_title", "JOB_TITLE_INVALID", "x" * 101),
    ("industry", "INDUSTRY_INVALID", "steel"),
)


@pytest.mark.parametrize(("field", "value", "message"), INVALID_VALUES)
def test_invalid_inputs_never_touch_the_database(field: str, value: Any, message: str) -> None:
    database = CountingDatabase()
    error = _error(database, _args(marker_row(), **{field: value}))
    assert str(error) == _message(message)
    assert database.calls == 0


@pytest.mark.parametrize("start", range(len(FIELD_MESSAGES)))
def test_inputs_are_checked_in_parameter_order(start: int) -> None:
    """Sind dieses und alle späteren Felder ungültig, nennt die Meldung das erste Feld der Reihenfolge."""
    database = CountingDatabase()
    bad = {field: value for field, _, value in FIELD_MESSAGES[start:]}
    error = _error(database, _args(marker_row(), **bad))
    assert str(error) == _message(FIELD_MESSAGES[start][1])
    assert database.calls == 0


def test_invalid_inputs_win_over_a_missing_database() -> None:
    from hoffmann_data.db import NOT_CONFIGURED

    assert str(_error(None, _args(marker_row(), country=None))) == _message("COUNTRY_INVALID")
    assert str(_error(None, _args(marker_row()))) == NOT_CONFIGURED


def test_optional_fields_may_be_missing_and_reach_the_database() -> None:
    """Position leer und Branche fehlend sind gültig: Der Aufruf kommt bis zur Datenbank (hier der Zähler)."""
    from hoffmann_data.db import DATABASE_ERROR

    for job_title in (None, "", "   "):
        database = CountingDatabase()
        assert (
            str(_error(database, _args(marker_row(), contact_job_title=job_title)))
            == DATABASE_ERROR
        )
        assert database.calls == 1


# 9. Transaktion und Fehler


def test_a_failure_after_the_customer_insert_stores_no_customer(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    """Der Kontakt-INSERT scheitert, der Kunde ist da schon eingefügt: Es bleibt kein Kunde ohne Kontakt."""
    from hoffmann_data.db import DATABASE_ERROR

    caplog.set_level(logging.DEBUG)
    row = marker_row()

    def explode_before_contact(text: str, params: Any) -> None:
        if _head(text).startswith("INSERT INTO CONTACTS"):
            raise RuntimeError("geheimtext")

    recorder = StatementRecorder(env.role, before_execute=explode_before_contact)
    error = _error(recorder, _args(row))
    assert str(error) == DATABASE_ERROR
    assert error.__cause__ is None
    assert recorder.connections == 1
    assert any(_head(s[0]).startswith("INSERT INTO CUSTOMERS") for s in recorder.statements), (
        "der Kunde war eingefügt"
    )
    assert _state(env.admin) == {"customers": [], "contacts": []}
    assert "geheimtext" not in _all_log_text(caplog)


def test_a_failing_commit_stores_nothing(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from hoffmann_data.db import DATABASE_ERROR

    row = marker_row()

    def failing_commit(self: Any) -> None:
        raise psycopg.OperationalError("geheimtext")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Connection, "commit", failing_commit)
        error = _error(env.role, _args(row))
    assert str(error) == DATABASE_ERROR
    assert "geheimtext" not in str(error)
    assert _state(env.admin) == {"customers": [], "contacts": []}


def test_an_unreachable_database_gives_the_fixed_error(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unerreichbarer Port (Linux: OperationalError, Windows: Zeitüberschreitung nach etwa 5 Sekunden)."""
    from hoffmann_data.db import DATABASE_ERROR, WriteDatabase

    caplog.set_level(logging.DEBUG)
    error = _error(WriteDatabase(UNREACHABLE_URL), _args(marker_row()))
    assert str(error) == DATABASE_ERROR
    captured = capsys.readouterr()
    for text in (str(error), _all_log_text(caplog), captured.out, captured.err):
        for secret in DB_SECRETS:
            assert secret not in text, secret


def test_without_configuration_the_tool_says_not_configured() -> None:
    from hoffmann_data.db import NOT_CONFIGURED

    assert str(_error(None, _args(marker_row()))) == NOT_CONFIGURED


# 10. Ende zu Ende über /mcp-write


def _wire(args: dict[str, Any]) -> dict[str, Any]:
    """Die Argumente für den Aufruf über HTTP: ohne die optionalen Felder, die None sind."""
    return {name: value for name, value in args.items() if value is not None}


def test_the_tool_call_returns_the_fixed_structure_and_writes_the_rows(
    env: Env, e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    row = marker_row()
    client = e2e_client_for(env.role)
    args = _wire(_args(row, industry="food", contact_job_title="Einkauf"))
    first = structured(result_of(tool_call_post(client, write_headers, TOOL, args)))
    assert first == _result(row["domain"], True, True)
    assert set(first) == OUTPUT_KEYS
    assert_no_internal_ids(first)
    customer = _customers(env.admin, row["domain"])[0]
    assert (customer["status"], customer["industry"]) == ("lead", "food")
    assert _contacts(env.admin, row["email"])[0]["job_title"] == "Einkauf"
    second = structured(result_of(tool_call_post(client, write_headers, TOOL, args)))
    assert second == _result(row["domain"], False, False)
    assert len(_state(env.admin)["customers"]) == 1


def test_the_read_token_on_the_write_path_stays_401(
    env: Env, e2e_client_for: Any, read_headers: dict[str, str]
) -> None:
    row = marker_row()
    client = e2e_client_for(env.role)
    wrong = tool_call_post(client, read_headers, TOOL, _wire(_args(row)))
    missing = tool_call_post(client, {}, TOOL, _wire(_args(row)))
    assert wrong.status_code == missing.status_code == 401
    assert wrong.content == missing.content and dict(wrong.headers) == dict(missing.headers)
    assert _state(env.admin) == {"customers": [], "contacts": []}


def test_the_write_token_on_the_read_path_stays_401(
    env: Env, e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    client = e2e_client_for(env.role)
    response = tool_call_post(client, write_headers, TOOL, _wire(_args(marker_row())), MCP_PATH)
    assert response.status_code == 401
    assert _state(env.admin) == {"customers": [], "contacts": []}


def test_create_lead_is_unknown_on_the_read_path(
    env: Env, e2e_client_for: Any, read_headers: dict[str, str]
) -> None:
    client = e2e_client_for(env.role)
    result = result_of(
        tool_call_post(client, read_headers, TOOL, _wire(_args(marker_row())), MCP_PATH)
    )
    assert result["isError"] is True
    assert not result.get("structuredContent")
    assert _state(env.admin) == {"customers": [], "contacts": []}


def test_missing_required_arguments_are_an_error_without_database_access(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    """Das SDK meldet fehlende Pflichtargumente selbst (Issue #21: es nennt dabei die Argumente); geprüft wird nur,
    dass es ein Fehler ist und die Datenbank nicht berührt wird."""
    database = CountingDatabase()
    client = e2e_client_for(database)
    for missing in REQUIRED:
        arguments = _wire(_args(marker_row()))
        del arguments[missing]
        result = result_of(tool_call_post(client, write_headers, TOOL, arguments))
        assert result["isError"] is True, missing
        assert not result.get("structuredContent")
    assert database.calls == 0


def test_an_invalid_value_is_rejected_with_the_fixed_message_before_the_database(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    database = CountingDatabase()
    client = e2e_client_for(database)
    result = result_of(
        tool_call_post(client, write_headers, TOOL, _wire(_args(marker_row(), language="fr")))
    )
    assert result["isError"] is True
    assert _message("LANGUAGE_INVALID") in error_text(result)
    assert not result.get("structuredContent")
    assert database.calls == 0


# 11. Kein Echo, kein Log


def test_marked_names_and_addresses_are_never_echoed_and_the_success_path_logs_nothing(
    env: Env,
    e2e_client_for: Any,
    write_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    row = marker_row()
    client = e2e_client_for(env.role)
    args = _args(
        row,
        company_name=f"{row['company_name']} {SECRET_MARKER}",
        contact_first_name=SECRET_MARKER,
        contact_last_name=f"{SECRET_MARKER} Muster",
        contact_email=f"{SECRET_MARKER}@{row['domain']}",
    )
    result = result_of(tool_call_post(client, write_headers, TOOL, _wire(args)))
    assert SECRET_MARKER.lower() not in json.dumps(result, ensure_ascii=False).lower()
    assert _customers(env.admin, row["domain"])[0]["company_name"].endswith(SECRET_MARKER), (
        "gespeichert ist er"
    )
    captured = capsys.readouterr()
    for text in (_all_log_text(caplog), captured.out, captured.err):
        assert SECRET_MARKER.lower() not in text.lower()
    # Der Weg über die App (Token, Weiche) ist hier nicht Gegenstand; geprüft wird der Datenbankteil des Erfolgspfads
    assert _db_records(caplog) == [], "der Erfolgspfad schreibt nichts ins Log der Datenbank"


def test_rejected_inputs_and_conflicts_never_reach_a_message_or_a_log(
    env: Env, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    known = marker_row()
    _existing(
        env.admin,
        {**known, "company_name": f"{known['company_name']} {SECRET_MARKER}"},
        contact=False,
    )
    new = marker_row()
    cases = [
        _args(new, company_name=SECRET_MARKER + "x" * 200),  # zu lang
        _args(new, contact_last_name=SECRET_MARKER + NUL),  # NUL
        _args(new, contact_email=f"{SECRET_MARKER}@firma.de"),  # nicht .example
        _args(
            new, company_name=f"{known['company_name']} {SECRET_MARKER}"
        ),  # Konflikt über den Namen
    ]
    for args in cases:
        error = _error(env.role, args)
        assert SECRET_MARKER.lower() not in str(error).lower()
    captured = capsys.readouterr()
    for text in (_all_log_text(caplog), captured.out, captured.err):
        assert SECRET_MARKER.lower() not in text.lower()
    assert _package_records(caplog) == [], "Eingabefehler und Konflikte schreiben nichts ins Log"


def test_a_database_error_does_not_echo_the_marker(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def explode(text: str, params: Any) -> None:
        if _head(text).startswith("INSERT INTO CUSTOMERS"):
            raise RuntimeError(SECRET_MARKER)

    error = _error(
        StatementRecorder(env.role, before_execute=explode),
        _args(marker_row(), contact_last_name=SECRET_MARKER),
    )
    assert SECRET_MARKER not in str(error)
    assert SECRET_MARKER not in _all_log_text(caplog)
    assert len(_db_records(caplog)) == 1


# 12. Beschreibung, Instructions, Schemas (N)


def _listed(e2e_client_for: Any, write_headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    client = e2e_client_for(CountingDatabase())
    tools = result_of(rpc_post(client, write_headers, "tools/list"))["tools"]
    return {tool["name"]: tool for tool in tools}


def test_the_description_is_no_placeholder_and_names_the_duplicate_protection(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    description = _listed(e2e_client_for, write_headers)[TOOL]["description"]
    assert "platzhalter" not in description.lower()
    for word in (*PARAMETERS, "lead", "other", ".example"):
        assert word in description, word
    assert "bestehender kunde wird nicht verändert" in description.lower()
    assert re.search(r"nichts.*überschrieben.*gelöscht", description, re.IGNORECASE | re.DOTALL)


def test_the_write_server_instructions_name_both_tools_as_real(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    client = e2e_client_for(CountingDatabase())
    instructions = (
        result_of(rpc_post(client, write_headers, "initialize", INITIALIZE)).get("instructions")
        or ""
    ).lower()
    assert TOOL in instructions and "log_activity" in instructions
    assert "platzhalter" not in instructions
    assert all(tool not in instructions for tool in READ_TOOLS)


def test_the_input_schema_has_exactly_the_eight_parameters_and_no_switch(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    schema = _listed(e2e_client_for, write_headers)[TOOL]["inputSchema"]
    assert set(schema["properties"]) == set(PARAMETERS)
    assert set(schema.get("required", [])) == set(REQUIRED)
    for name in schema["properties"]:
        assert not any(word in name for word in ("status", "domain", "force", "skip", "dup")), name


def test_the_function_has_no_switch_to_turn_the_duplicate_check_off() -> None:
    from hoffmann_data import crm

    parameters = list(inspect.signature(crm.create_lead).parameters)
    assert parameters == ["database", *PARAMETERS]


def test_the_output_schema_has_exactly_the_three_fields(
    e2e_client_for: Any, write_headers: dict[str, str]
) -> None:
    output = _listed(e2e_client_for, write_headers)[TOOL].get("outputSchema")
    assert isinstance(output, dict) and output.get("type") == "object"
    properties = output.get("properties", {})
    assert set(properties) == OUTPUT_KEYS
    assert properties["customer_created"]["type"] == "boolean"
    assert properties["contact_created"]["type"] == "boolean"
    assert properties["customer_domain"]["type"] == "string"
