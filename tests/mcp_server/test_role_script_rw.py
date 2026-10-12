"""Tests des Rollen-Skripts db/roles/data_service_rw.sql (F08, ADR 0005, Etappe 1).

Die Schreibrolle darf Leads und Aktivitäten anlegen, sonst nichts (Auftrag 5.1): INSERT und SELECT auf
customers, contacts und activities, je eine SELECT- und eine INSERT-Policy (die INSERT-Policy mit WITH CHECK),
kein UPDATE, DELETE, TRUNCATE, kein Zugriff auf products, product_fits und discount_rules. Die Tests ohne
Datenbank prüfen den Text des Skripts, die übrigen führen es unter einem Zufallsnamen in der Test-Transaktion
aus (Fixture rw_role) und probieren die Rolle mit SET LOCAL ROLE. Alles wird am Ende des Tests zurückgerollt.

Diese Tests sind rot, bis das Skript existiert (TDD). Die feste Zahl der Vorkommen des Rollennamens kommt
erst im grünen Schritt.
"""

import os
import re
import uuid
from typing import Any

import psycopg
import pytest
from mcp_testkit import (
    ROLE_NAME,
    WRITE_ROLE_NAME,
    WRITE_ROLE_SCRIPT,
    as_role,
    render_role_script,
    render_write_role_script,
)
from psycopg import errors, sql

WRITE_TABLES = ("customers", "contacts", "activities")
FORBIDDEN_TABLES = ("products", "product_fits", "discount_rules")
SCHEMA_TABLES = (
    "products",
    "product_fits",
    "customers",
    "contacts",
    "activities",
    "discount_rules",
)
# Je Tabelle eine Spalte für die Spaltenrechte
COLUMN_OF = {
    "customers": "company_name",
    "contacts": "first_name",
    "activities": "subject",
}
TABLE_RIGHTS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_RIGHTS = ("SELECT", "INSERT", "UPDATE", "REFERENCES")


def _code(text: str) -> str:
    """Das Skript ohne Kommentarzeilen (-- bis Zeilenende)."""
    return re.sub(r"--[^\n]*", "", text)


def _script_code() -> str:
    return _code(WRITE_ROLE_SCRIPT.read_text(encoding="utf-8"))


# Ohne Datenbank: der Text des Skripts


def test_write_role_script_exists() -> None:
    assert WRITE_ROLE_SCRIPT.is_file(), f"{WRITE_ROLE_SCRIPT} fehlt"


def test_role_name_replacement_hits_every_occurrence() -> None:
    """Die Ersetzung des Namens im Test greift überall; das getestete Skript ist das echte Skript."""
    original = WRITE_ROLE_SCRIPT.read_text(encoding="utf-8")
    count = original.count(WRITE_ROLE_NAME)
    assert count > 0
    probe = render_write_role_script("rw_probe_abc")
    assert WRITE_ROLE_NAME not in probe
    assert probe.count("rw_probe_abc") == count
    # Mit dem Originalnamen gerendert ist der Text gleich der Datei: die einzige Differenz ist der Name
    assert render_write_role_script(WRITE_ROLE_NAME) == original
    assert probe.replace("rw_probe_abc", WRITE_ROLE_NAME) == original


def test_role_script_names_no_other_role() -> None:
    """Alle Ziele von ROLE und TO sind die Rolle selbst (PUBLIC ausgenommen), auch nicht die Leserolle."""
    code = _script_code()
    targets = set(re.findall(r"\b(?:ROLE|TO)\s+(\w+)", code))
    assert targets <= {WRITE_ROLE_NAME, "PUBLIC"}, targets
    assert WRITE_ROLE_NAME in targets
    assert ROLE_NAME not in WRITE_ROLE_SCRIPT.read_text(encoding="utf-8")


def test_role_script_revokes_only_from_the_role_or_public() -> None:
    targets = set(re.findall(r"\bREVOKE\b[^;]*?\bFROM\s+(\w+)", _script_code(), re.I))
    assert targets, "das Skript entzieht keine Rechte (REVOKE fehlt)"
    assert targets <= {WRITE_ROLE_NAME, "PUBLIC"}, targets


def test_role_script_has_no_password() -> None:
    """Das Passwort setzt der Mensch separat und es steht nie im Repository."""
    assert not re.search(r"\bPASSWORD\b", _script_code(), re.I)


def test_role_script_does_not_name_the_superuser_option() -> None:
    """Auch NOSUPERUSER bricht für einen Nicht-Superuser ab (Supabase SQL-Editor, PostgreSQL ab 16)."""
    assert "superuser" not in _script_code().lower()


def test_role_script_grants_only_usage_select_and_insert() -> None:
    code = _script_code()
    granted = {word.upper() for word in re.findall(r"\bGRANT\s+(\w+)", code, re.I)}
    assert granted == {"USAGE", "SELECT", "INSERT"}
    assert not re.search(r"\b(UPDATE|DELETE|TRUNCATE)\b", code, re.I)


def test_role_script_does_not_mention_the_forbidden_tables() -> None:
    code = _script_code()
    for table in FORBIDDEN_TABLES:
        assert not re.search(rf"\b{table}\b", code), table


# Mit Datenbank: die Rolle


def test_role_has_no_dangerous_attributes(conn: psycopg.Connection[Any], rw_role: str) -> None:
    row = conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin "
        "FROM pg_roles WHERE rolname = %s",
        (rw_role,),
    ).fetchone()
    assert row == (False, False, False, False, False, True)


def test_role_is_not_read_only_by_default_and_time_limited(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    found = conn.execute("SELECT rolconfig FROM pg_roles WHERE rolname = %s", (rw_role,)).fetchone()
    assert found is not None
    config = found[0] or []
    assert not any(entry.startswith("default_transaction_read_only") for entry in config), config
    assert "statement_timeout=5s" in config


def test_script_can_run_twice_without_duplicating_policies(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    def policy_count() -> int:
        found = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema() AND %s = ANY(roles)",
            (rw_role,),
        ).fetchone()
        assert found is not None
        return int(found[0])

    assert policy_count() == 2 * len(WRITE_TABLES)
    conn.execute(render_write_role_script(rw_role))
    assert policy_count() == 2 * len(WRITE_TABLES)


def test_each_table_has_exactly_one_select_and_one_insert_policy(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    rows = conn.execute(
        "SELECT tablename, cmd, permissive, with_check IS NOT NULL FROM pg_policies "
        "WHERE schemaname = current_schema() AND %s = ANY(roles) ORDER BY tablename, cmd",
        (rw_role,),
    ).fetchall()
    expected = []
    for table in sorted(WRITE_TABLES):
        expected.append((table, "INSERT", "PERMISSIVE", True))
        expected.append((table, "SELECT", "PERMISSIVE", False))
    assert rows == expected


# Mit Datenbank: was die Rolle darf und nicht darf


def _insert_one_row(conn: psycopg.Connection[Any], table: str, parent: int | None) -> int:
    """Legt als aktuelle Rolle eine gültige Minimalzeile an und gibt ihre id zurück (RETURNING braucht SELECT)."""
    suffix = uuid.uuid4().hex[:10]
    if table == "customers":
        found = conn.execute(
            "INSERT INTO customers (company_name, domain, industry, country, status) "
            "VALUES (%s, %s, 'other', 'de', 'lead') RETURNING id",
            (f"Schreibtest {suffix}", f"schreibtest-{suffix}.example"),
        ).fetchone()
    elif table == "contacts":
        found = conn.execute(
            "INSERT INTO contacts (customer_id, first_name, last_name, email, language) "
            "VALUES (%s, 'Erika', 'Muster', %s, 'de') RETURNING id",
            (parent, f"kontakt-{suffix}@schreibtest.example"),
        ).fetchone()
    else:
        found = conn.execute(
            "INSERT INTO activities (customer_id, type, occurred_at, subject, summary, created_by) "
            "VALUES (%s, 'inquiry', now(), 'Betreff', 'Zusammenfassung', 'agent') RETURNING id",
            (parent,),
        ).fetchone()
    assert found is not None
    return int(found[0])


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_role_can_insert_and_select(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, table: str
) -> None:
    """Belegt auch die Annahme aus ADR 0005: Identity-Spalten brauchen kein eigenes Sequenzrecht.

    Das Skript vergibt keine Rechte an Sequenzen, und das INSERT mit RETURNING gelingt trotzdem.
    """
    parent = make.customer()
    with as_role(conn, rw_role):
        new_id = _insert_one_row(conn, table, parent)
        found = conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE id = %s").format(sql.Identifier(table)),
            (new_id,),
        ).fetchone()
    assert found == (1,)


@pytest.mark.parametrize("table", WRITE_TABLES)
@pytest.mark.parametrize(
    "statement",
    [
        # DEFAULT statt "id = id": Bei GENERATED ALWAYS käme sonst ein anderer Fehler vor der Rechteprüfung
        "UPDATE {} SET id = DEFAULT WHERE false",
        "DELETE FROM {} WHERE false",
        "TRUNCATE {}",
    ],
    ids=["update", "delete", "truncate"],
)
def test_role_cannot_update_delete_or_truncate(
    conn: psycopg.Connection[Any], rw_role: str, table: str, statement: str
) -> None:
    query = sql.SQL(statement).format(sql.Identifier(table))
    with as_role(conn, rw_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(query)


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_role_cannot_update_a_column(
    conn: psycopg.Connection[Any], rw_role: str, table: str
) -> None:
    """Auch ein UPDATE einer einzelnen Spalte scheitert (Spaltenrecht)."""
    column = sql.Identifier(COLUMN_OF[table])
    query = sql.SQL("UPDATE {} SET {} = {} WHERE false").format(
        sql.Identifier(table), column, column
    )
    with as_role(conn, rw_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(query)


@pytest.mark.parametrize("table", FORBIDDEN_TABLES)
@pytest.mark.parametrize(
    "statement",
    ["SELECT count(*) FROM {}", "INSERT INTO {} DEFAULT VALUES"],
    ids=["read", "insert"],
)
def test_role_has_no_access_to_the_forbidden_tables(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, table: str, statement: str
) -> None:
    make.rule()
    query = sql.SQL(statement).format(sql.Identifier(table))
    with as_role(conn, rw_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(query)


def test_role_cannot_create_objects(conn: psycopg.Connection[Any], rw_role: str) -> None:
    with as_role(conn, rw_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("CREATE TABLE angelegt_von_rw (a integer)")


def _policy_name(conn: psycopg.Connection[Any], role: str, table: str, command: str) -> str:
    found = conn.execute(
        "SELECT policyname FROM pg_policies "
        "WHERE schemaname = current_schema() AND tablename = %s AND cmd = %s AND %s = ANY(roles)",
        (table, command, role),
    ).fetchall()
    assert len(found) == 1, (table, command, found)
    return str(found[0][0])


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_without_the_select_policy_no_row_is_visible(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, table: str
) -> None:
    """Gegenprobe: Mit SELECT-Recht, aber ohne Policy sieht die Rolle keine Zeile."""
    customer = make.customer()
    contact = make.contact(customer)
    make.activity(customer, contact_id=contact)
    name = _policy_name(conn, rw_role, table, "SELECT")
    conn.execute(
        sql.SQL("DROP POLICY {} ON {}").format(sql.Identifier(name), sql.Identifier(table))
    )
    with as_role(conn, rw_role):
        found = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        assert found.fetchone() == (0,)


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_without_the_insert_policy_no_row_can_be_inserted(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, table: str
) -> None:
    parent = make.customer()
    name = _policy_name(conn, rw_role, table, "INSERT")
    conn.execute(
        sql.SQL("DROP POLICY {} ON {}").format(sql.Identifier(name), sql.Identifier(table))
    )
    with as_role(conn, rw_role):
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                _insert_one_row(conn, table, parent)


# Mit Datenbank: die beiden Rollen-Skripte beeinflussen sich nicht


def _snapshot(conn: psycopg.Connection[Any], role: str) -> dict[str, Any]:
    """Alles, was Rechte, Attribute und Policies einer Rolle ausmacht, als vergleichbarer Wert."""
    attributes = conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin, "
        "rolconfig FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    table_rights: dict[tuple[str, str], bool] = {}
    column_rights: dict[tuple[str, str], bool] = {}
    for table in SCHEMA_TABLES:
        for right in TABLE_RIGHTS:
            found = conn.execute(
                "SELECT has_table_privilege(%s::name, to_regclass(%s::text), %s::text)", (role, table, right)
            ).fetchone()
            assert found is not None
            table_rights[(table, right)] = bool(found[0])
        for right in COLUMN_RIGHTS:
            found = conn.execute(
                "SELECT has_any_column_privilege(%s::name, to_regclass(%s::text), %s::text)",
                (role, table, right),
            ).fetchone()
            assert found is not None
            column_rights[(table, right)] = bool(found[0])
    schema_rights = conn.execute(
        "SELECT has_schema_privilege(%s::name, current_schema(), 'USAGE'), "
        "has_schema_privilege(%s::name, current_schema(), 'CREATE')",
        (role, role),
    ).fetchone()
    policies = conn.execute(
        "SELECT tablename, policyname, cmd, permissive, qual, with_check FROM pg_policies "
        "WHERE schemaname = current_schema() AND %s = ANY(roles) ORDER BY tablename, policyname",
        (role,),
    ).fetchall()
    return {
        "attributes": attributes,
        "table_rights": table_rights,
        "column_rights": column_rights,
        "schema_rights": schema_rights,
        "policies": policies,
    }


def test_applying_the_write_script_leaves_the_read_role_unchanged(
    conn: psycopg.Connection[Any], ro_role: str
) -> None:
    before = _snapshot(conn, ro_role)
    assert before["policies"], "die Leserolle hat Policies (sonst belegt der Test nichts)"
    other = f"rw_test_{uuid.uuid4().hex[:12]}"
    conn.execute(render_write_role_script(other))
    conn.execute(render_write_role_script(other))
    assert _snapshot(conn, ro_role) == before


def test_applying_the_read_script_leaves_the_write_role_unchanged(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    before = _snapshot(conn, rw_role)
    assert before["policies"], "die Schreibrolle hat Policies (sonst belegt der Test nichts)"
    other = f"ro_test_{uuid.uuid4().hex[:12]}"
    conn.execute(render_role_script(other))
    conn.execute(render_role_script(other))
    assert _snapshot(conn, rw_role) == before


# Mit Datenbank: Ausführung als Nicht-Superuser (bildet den Supabase SQL-Editor nach)


@pytest.fixture
def require_superuser_test_user(conn: psycopg.Connection[Any]) -> None:
    """REPLICATION und BYPASSRLS an die Hilfsrolle vergeben kann nur ein Superuser."""
    row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    if row is not None and row[0]:
        return
    message = "Der Testbenutzer ist kein Superuser: Dieser Fall braucht einen Superuser."
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


def test_script_runs_twice_as_a_non_superuser_with_hosting_attributes(
    conn: psycopg.Connection[Any], db_schema: str, require_superuser_test_user: None
) -> None:
    """Supabase führt das Skript als postgres aus: kein Superuser, aber CREATEROLE, CREATEDB, REPLICATION,
    BYPASSRLS und Eigentümer der Tabellen. Beide Läufe (neue und vorhandene Rolle) müssen fehlerfrei sein."""
    hosting = f"hosting_{uuid.uuid4().hex[:12]}"
    rw = f"rw_test_{uuid.uuid4().hex[:12]}"
    script = render_write_role_script(rw)
    conn.execute(
        sql.SQL("CREATE ROLE {} LOGIN CREATEROLE CREATEDB REPLICATION BYPASSRLS").format(
            sql.Identifier(hosting)
        )
    )
    conn.execute(
        sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
            sql.Identifier(db_schema), sql.Identifier(hosting)
        )
    )
    for table in SCHEMA_TABLES:
        conn.execute(
            sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                sql.Identifier(db_schema), sql.Identifier(table), sql.Identifier(hosting)
            )
        )
    with as_role(conn, hosting):
        for _ in range(2):
            # Eigene Teiltransaktion: Scheitert ein Lauf, bleibt die Transaktion für RESET ROLE benutzbar
            # und die eigentliche Ausnahme geht nicht in einem Folgefehler unter.
            with conn.transaction():
                conn.execute(script)

    row = conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication, rolcanlogin, "
        "rolconfig FROM pg_roles WHERE rolname = %s",
        (rw,),
    ).fetchone()
    assert row is not None
    assert row[:6] == (False, False, False, False, False, True)
    config = row[6] or []
    assert not any(entry.startswith("default_transaction_read_only") for entry in config)
    assert "statement_timeout=5s" in config
    policies = conn.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema() AND %s = ANY(roles)",
        (rw,),
    ).fetchone()
    assert policies == (2 * len(WRITE_TABLES),)
