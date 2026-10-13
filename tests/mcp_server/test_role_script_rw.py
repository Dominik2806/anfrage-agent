"""Tests des Rollen-Skripts db/roles/data_service_rw.sql (F08, ADR 0005, Etappe 1).

Die Schreibrolle darf Leads und Aktivitäten anlegen, sonst nichts (Auftrag 5.1): INSERT und SELECT auf
customers und contacts, auf activities nur INSERT (Etappe 5b), je eine INSERT-Policy mit WITH CHECK und eine
SELECT-Policy auf customers und contacts, kein UPDATE, DELETE, TRUNCATE, kein Zugriff auf products, product_fits
und discount_rules. Die Tests ohne
Datenbank prüfen den Text des Skripts, die übrigen führen es unter einem Zufallsnamen in der Test-Transaktion
aus (Fixture rw_role) und probieren die Rolle mit SET LOCAL ROLE. Alles wird am Ende des Tests zurückgerollt.

Diese Tests sind rot, bis das Skript existiert (TDD). Die feste Zahl der Vorkommen des Rollennamens kommt
erst im grünen Schritt.
"""

import os
import re
import uuid
from collections.abc import Callable
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
# Etappe 5b: Lesen und anlegen nur hier; auf activities nur anlegen
READ_WRITE_TABLES = ("customers", "contacts")
POLICY_COUNT = len(READ_WRITE_TABLES) + len(WRITE_TABLES)
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


def test_role_script_grants_select_only_on_customers_and_contacts() -> None:
    """Etappe 5b: activities wird nur angelegt, nie gelesen. Die DROP-Zeile bleibt und räumt den alten Stand auf."""
    code = _script_code()
    select_tables = set()
    for tables in re.findall(r"\bGRANT\s+SELECT\s+ON\s+([^;]*?)\s+TO\b", code, re.I):
        select_tables |= {name.strip() for name in tables.split(",")}
    assert select_tables == set(READ_WRITE_TABLES), select_tables
    assert not re.search(r"CREATE\s+POLICY\s+rw_select\s+ON\s+activities\b", code, re.I)
    assert re.search(r"DROP\s+POLICY\s+IF\s+EXISTS\s+rw_select\s+ON\s+activities\s*;", code, re.I)


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

    assert policy_count() == POLICY_COUNT
    conn.execute(render_write_role_script(rw_role))
    assert policy_count() == POLICY_COUNT


def test_policies_are_one_insert_per_table_and_one_select_for_customers_and_contacts(
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
        if table in READ_WRITE_TABLES:
            expected.append((table, "SELECT", "PERMISSIVE", False))
    assert rows == expected


def test_applying_the_script_again_takes_the_old_select_on_activities_away(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    """Ein Betrieb mit dem Stand vor Etappe 5b: SELECT-Recht und Policy auf activities sind vorhanden.

    Das erneute Ausführen des Skripts entzieht das Recht (REVOKE ALL) und löscht die Policy (DROP POLICY IF EXISTS).
    """
    role = sql.Identifier(rw_role)
    # Die Vorbereitung gilt unabhängig vom Stand des Skripts: GRANT ist wiederholbar, die Policy wird ersetzt
    conn.execute(sql.SQL("GRANT SELECT ON activities TO {}").format(role))
    conn.execute("DROP POLICY IF EXISTS rw_select ON activities")
    conn.execute(
        sql.SQL("CREATE POLICY rw_select ON activities FOR SELECT TO {} USING (true)").format(role)
    )
    conn.execute(render_write_role_script(rw_role))
    rights = conn.execute(
        "SELECT has_table_privilege(%s::name, to_regclass('activities'), 'SELECT'), "
        "has_any_column_privilege(%s::name, to_regclass('activities'), 'SELECT')",
        (rw_role, rw_role),
    ).fetchone()
    assert rights == (False, False)
    left = conn.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema() "
        "AND tablename = 'activities' AND cmd = 'SELECT' AND %s = ANY(roles)",
        (rw_role,),
    ).fetchone()
    assert left == (0,)
    total = conn.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname = current_schema() AND %s = ANY(roles)",
        (rw_role,),
    ).fetchone()
    assert total == (POLICY_COUNT,)


# Mit Datenbank: was die Rolle darf und nicht darf


def _insert_one_row(conn: psycopg.Connection[Any], table: str, parent: int | None) -> int | None:
    """Legt als aktuelle Rolle eine gültige Minimalzeile an.

    Auf customers und contacts kommt die id zurück (RETURNING braucht SELECT). Auf activities hat die Rolle kein
    SELECT (Etappe 5b): kein RETURNING, Rückgabe None. Eine Ablehnung kommt so von der Policy, nicht vom Recht.
    """
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
        conn.execute(
            "INSERT INTO activities (customer_id, type, occurred_at, subject, summary, created_by) "
            "VALUES (%s, 'inquiry', now(), 'Betreff', 'Zusammenfassung', 'agent')",
            (parent,),
        )
        return None
    assert found is not None
    return int(found[0])


@pytest.mark.parametrize("table", READ_WRITE_TABLES)
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


def test_role_can_insert_an_activity_without_reading_it(
    conn: psycopg.Connection[Any], rw_role: str, make: Any
) -> None:
    """Auf activities legt die Rolle an, ohne die Zeile lesen zu können (gezählt wird als Eigentümer)."""
    parent = make.customer()
    before = _count_rows(conn, "activities")
    with as_role(conn, rw_role):
        assert _insert_one_row(conn, "activities", parent) is None
    assert _count_rows(conn, "activities") == before + 1


def test_role_has_no_select_privilege_on_activities(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    """Kein SELECT auf activities, weder als Tabellen- noch als Spaltenrecht; INSERT bleibt (Etappe 5b)."""
    table_select = conn.execute(
        "SELECT has_table_privilege(%s::name, to_regclass('activities'), 'SELECT'), "
        "has_any_column_privilege(%s::name, to_regclass('activities'), 'SELECT'), "
        "has_table_privilege(%s::name, to_regclass('activities'), 'INSERT')",
        (rw_role, rw_role, rw_role),
    ).fetchone()
    assert table_select == (False, False, True)


def test_role_cannot_read_activities(
    conn: psycopg.Connection[Any], rw_role: str, make: Any
) -> None:
    """Weder ein SELECT noch ein INSERT mit RETURNING (das braucht SELECT) gelingt unter der Rolle."""
    parent = make.customer()
    with as_role(conn, rw_role):
        for statement in (
            "SELECT count(*) FROM activities",
            "INSERT INTO activities (customer_id, type, occurred_at, subject, summary, created_by) "
            "VALUES (%s, 'inquiry', now(), 'Betreff', 'Zusammenfassung', 'agent') RETURNING id",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(statement, (parent,) if "%s" in statement else None)


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


@pytest.mark.parametrize("table", READ_WRITE_TABLES)
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
                "SELECT has_table_privilege(%s::name, to_regclass(%s::text), %s::text)",
                (role, table, right),
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
    assert policies == (POLICY_COUNT,)


# Mit Datenbank: Die INSERT-Policies beschränken, was die Rolle anlegen darf (ADR 0005, Punkt 7)
#
# Die Fälle erfüllen alle Tabellen-CHECKs aus schema.sql (gültiger status, created_by und type, amount_eur NULL).
# Ein Fehler kann deshalb nur von der Policy kommen: Ein CHECK-Verstoß hätte SQLSTATE 23514 (CheckViolation),
# und das Tabellenrecht INSERT hat die Rolle.

ALLOWED_ACTIVITY_TYPES = ("inquiry", "complaint", "note")


def _count_rows(conn: psycopg.Connection[Any], table: str) -> int:
    found = conn.execute(
        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    ).fetchone()
    assert found is not None
    return int(found[0])


def _insert_customer(conn: psycopg.Connection[Any], status: str) -> int:
    suffix = uuid.uuid4().hex[:10]
    found = conn.execute(
        "INSERT INTO customers (company_name, domain, industry, country, status) "
        "VALUES (%s, %s, 'other', 'de', %s) RETURNING id",
        (f"Richtlinientest {suffix}", f"richtlinientest-{suffix}.example", status),
    ).fetchone()
    assert found is not None
    return int(found[0])


def _insert_activity(
    conn: psycopg.Connection[Any],
    customer_id: int,
    activity_type: str,
    created_by: str,
    product_id: int | None = None,
) -> None:
    """Ohne RETURNING: Die Rolle hat auf activities kein SELECT, eine Ablehnung soll von der Policy kommen."""
    conn.execute(
        "INSERT INTO activities "
        "(customer_id, product_id, type, occurred_at, subject, summary, created_by) "
        "VALUES (%s, %s, %s, now(), 'Betreff', 'Zusammenfassung', %s)",
        (customer_id, product_id, activity_type, created_by),
    )


def _assert_refused(
    conn: psycopg.Connection[Any], role: str, table: str, action: Callable[[], object]
) -> None:
    """`action` läuft unter der Rolle im Savepoint und scheitert mit SQLSTATE 42501; es bleibt keine Zeile zurück.

    Gezählt wird außerhalb von as_role: Der Testbenutzer ist Eigentümer und sieht alle Zeilen.
    """
    before = _count_rows(conn, table)
    with as_role(conn, role):
        with pytest.raises(errors.InsufficientPrivilege) as info:
            with conn.transaction():
                action()
    assert info.value.sqlstate == "42501"
    assert _count_rows(conn, table) == before


@pytest.mark.parametrize("status", ["existing", "inactive"])
def test_role_cannot_insert_a_customer_with_a_status_other_than_lead(
    conn: psycopg.Connection[Any], rw_role: str, status: str
) -> None:
    _assert_refused(conn, rw_role, "customers", lambda: _insert_customer(conn, status))


@pytest.mark.parametrize("created_by", ["seed", "staff"])
def test_role_cannot_insert_an_activity_with_a_created_by_other_than_agent(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, created_by: str
) -> None:
    customer = make.customer()
    _assert_refused(
        conn,
        rw_role,
        "activities",
        lambda: _insert_activity(conn, customer, "inquiry", created_by),
    )


@pytest.mark.parametrize("activity_type", ["quote", "order", "service"])
def test_role_cannot_insert_an_activity_with_a_type_outside_the_allowed_three(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, activity_type: str
) -> None:
    customer = make.customer()
    _assert_refused(
        conn,
        rw_role,
        "activities",
        lambda: _insert_activity(conn, customer, activity_type, "agent"),
    )


def test_role_cannot_insert_an_activity_with_a_product(
    conn: psycopg.Connection[Any], rw_role: str, make: Any
) -> None:
    customer = make.customer()
    product = make.product()
    _assert_refused(
        conn,
        rw_role,
        "activities",
        lambda: _insert_activity(conn, customer, "inquiry", "agent", product),
    )


def test_role_can_insert_a_customer_with_status_lead(
    conn: psycopg.Connection[Any], rw_role: str
) -> None:
    with as_role(conn, rw_role):
        new_id = _insert_customer(conn, "lead")
        found = conn.execute("SELECT count(*) FROM customers WHERE id = %s", (new_id,)).fetchone()
    assert found == (1,)


@pytest.mark.parametrize("activity_type", ALLOWED_ACTIVITY_TYPES)
def test_role_can_insert_an_activity_as_agent_with_each_allowed_type(
    conn: psycopg.Connection[Any], rw_role: str, make: Any, activity_type: str
) -> None:
    customer = make.customer()
    before = _count_rows(conn, "activities")
    with as_role(conn, rw_role):
        _insert_activity(conn, customer, activity_type, "agent")
    # Gezählt wird als Eigentümer: Die Rolle selbst darf activities nicht lesen
    assert _count_rows(conn, "activities") == before + 1
