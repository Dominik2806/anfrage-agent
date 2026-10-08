"""Tests des Rollen-Skripts db/roles/data_service_ro.sql (F07).

Die Rolle darf nur lesen (Auftrag 5.1): SELECT auf fünf Tabellen, dazu je eine SELECT-Policy, weil Row
Level Security ohne Policies nichts herausgibt. Die Tests ohne Datenbank prüfen den Text des Skripts, die
übrigen führen es unter einem Zufallsnamen in der Test-Transaktion aus (Fixture ro_role) und probieren
die Rolle mit SET LOCAL ROLE. Alles wird am Ende des Tests zurückgerollt.
"""

import re
from typing import Any

import psycopg
import pytest
from mcp_testkit import (
    EXPECTED_ROLE_NAME_COUNT,
    READABLE_TABLES,
    ROLE_NAME,
    ROLE_SCRIPT,
    as_role,
    render_role_script,
)
from psycopg import errors, sql


def _code(text: str) -> str:
    """Das Skript ohne Kommentarzeilen (-- bis Zeilenende)."""
    return re.sub(r"--[^\n]*", "", text)


# Ohne Datenbank: der Text des Skripts


def test_role_script_exists() -> None:
    assert ROLE_SCRIPT.is_file(), f"{ROLE_SCRIPT} fehlt"


def test_role_name_replacement_hits_every_occurrence() -> None:
    """Die Ersetzung des Namens im Test greift überall; das getestete Skript ist das echte Skript."""
    original = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert original.count(ROLE_NAME) == EXPECTED_ROLE_NAME_COUNT > 0
    probe = render_role_script("ro_probe_abc")
    assert ROLE_NAME not in probe
    assert probe.count("ro_probe_abc") == EXPECTED_ROLE_NAME_COUNT
    # Mit dem Originalnamen gerendert ist der Text gleich der Datei: die einzige Differenz ist der Name
    assert render_role_script(ROLE_NAME) == original
    assert probe.replace("ro_probe_abc", ROLE_NAME) == original


def test_role_script_names_no_other_role() -> None:
    """Alle Ziele von ROLE und TO sind die Rolle selbst (PUBLIC ausgenommen), sonst griffe die Ersetzung zu kurz."""
    targets = set(
        re.findall(r"\b(?:ROLE|TO)\s+(\w+)", _code(ROLE_SCRIPT.read_text(encoding="utf-8")))
    )
    assert targets <= {ROLE_NAME, "PUBLIC"}, targets
    assert ROLE_NAME in targets


def test_role_script_has_no_password() -> None:
    """Das Passwort setzt der Mensch separat und es steht nie im Repository."""
    assert not re.search(r"\bPASSWORD\b", _code(ROLE_SCRIPT.read_text(encoding="utf-8")), re.I)


def test_role_script_grants_only_usage_and_select() -> None:
    code = _code(ROLE_SCRIPT.read_text(encoding="utf-8"))
    granted = {word.upper() for word in re.findall(r"\bGRANT\s+(\w+)", code, re.I)}
    assert granted == {"USAGE", "SELECT"}
    assert "discount_rules" not in code, "discount_rules bleibt bis F09 ohne Zugriff"
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", code, re.I)


# Mit Datenbank: die Rolle


def test_role_has_no_dangerous_attributes(conn: psycopg.Connection[Any], ro_role: str) -> None:
    row = conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin "
        "FROM pg_roles WHERE rolname = %s",
        (ro_role,),
    ).fetchone()
    assert row == (False, False, False, False, False, True)


def test_role_is_read_only_and_time_limited_by_default(
    conn: psycopg.Connection[Any], ro_role: str
) -> None:
    found = conn.execute("SELECT rolconfig FROM pg_roles WHERE rolname = %s", (ro_role,)).fetchone()
    assert found is not None
    config = found[0] or []
    assert "default_transaction_read_only=on" in config
    assert "statement_timeout=5s" in config


def test_script_can_run_twice_without_duplicating_policies(
    conn: psycopg.Connection[Any], ro_role: str
) -> None:
    def policy_count() -> int:
        found = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema() AND %s = ANY(roles)",
            (ro_role,),
        ).fetchone()
        assert found is not None
        return int(found[0])

    assert policy_count() == len(READABLE_TABLES)
    conn.execute(render_role_script(ro_role))
    assert policy_count() == len(READABLE_TABLES)


def test_each_readable_table_has_exactly_one_select_policy(
    conn: psycopg.Connection[Any], ro_role: str
) -> None:
    rows = conn.execute(
        "SELECT tablename, cmd, permissive FROM pg_policies "
        "WHERE schemaname = current_schema() AND %s = ANY(roles) ORDER BY tablename",
        (ro_role,),
    ).fetchall()
    assert rows == [(table, "SELECT", "PERMISSIVE") for table in sorted(READABLE_TABLES)]


# Mit Datenbank: was die Rolle darf und nicht darf


def _seed_one_row_per_readable_table(make: Any) -> None:
    customer = make.customer()
    contact = make.contact(customer)
    part = make.product()
    plant = make.product()
    make.fit(part, plant)
    make.activity(customer, contact_id=contact, product_id=part)


def test_role_reads_every_readable_table(
    conn: psycopg.Connection[Any], ro_role: str, make: Any
) -> None:
    _seed_one_row_per_readable_table(make)
    with as_role(conn, ro_role):
        for table in READABLE_TABLES:
            found = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            row = found.fetchone()
            assert row is not None and row[0] >= 1, table


@pytest.mark.parametrize("table", READABLE_TABLES)
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO {} DEFAULT VALUES",
        "UPDATE {} SET id = id WHERE false",
        "DELETE FROM {} WHERE false",
        "TRUNCATE {}",
    ],
    ids=["insert", "update", "delete", "truncate"],
)
def test_role_cannot_write(
    conn: psycopg.Connection[Any], ro_role: str, table: str, statement: str
) -> None:
    query = sql.SQL(statement).format(sql.Identifier(table))
    with as_role(conn, ro_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(query)


def test_role_cannot_create_objects(conn: psycopg.Connection[Any], ro_role: str) -> None:
    with as_role(conn, ro_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("CREATE TABLE angelegt_von_ro (a integer)")


def test_role_cannot_read_discount_rules(
    conn: psycopg.Connection[Any], ro_role: str, make: Any
) -> None:
    make.rule()
    with as_role(conn, ro_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT * FROM discount_rules")


def test_policies_are_what_makes_rows_visible(
    conn: psycopg.Connection[Any], ro_role: str, make: Any
) -> None:
    """Gegenprobe: Mit SELECT-Recht, aber ohne Policy sieht die Rolle keine Zeile."""
    make.product()
    with as_role(conn, ro_role):
        before = conn.execute("SELECT count(*) FROM products").fetchone()
    assert before is not None and before[0] >= 1

    names = conn.execute(
        "SELECT policyname FROM pg_policies "
        "WHERE schemaname = current_schema() AND tablename = 'products' AND %s = ANY(roles)",
        (ro_role,),
    ).fetchall()
    assert len(names) == 1
    conn.execute(
        sql.SQL("DROP POLICY {} ON products").format(sql.Identifier(names[0][0])),
    )
    with as_role(conn, ro_role):
        after = conn.execute("SELECT count(*) FROM products").fetchone()
    assert after == (0,)
