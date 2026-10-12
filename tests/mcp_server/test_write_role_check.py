"""Tests der Startprüfung der Schreibrolle: hoffmann_data/db.py, verify_write_role (F08, ADR 0005, Etappe 1).

Die Schreibrolle (db/roles/data_service_rw.sql) darf auf customers, contacts und activities genau SELECT und
INSERT, auf products, product_fits und discount_rules nichts. Die Prüfung ist eine Momentaufnahme beim Start;
die Datenbank bleibt die eigentliche Sperre. Es gibt keinen Schalter. Diese Tests sind rot, bis verify_write_role
und das Rollen-Skript existieren (TDD). Nicht in dieser Etappe: main() und Konfiguration.

Schnittstelle, die diese Tests festlegen (Form wie verify_read_only_role, test_role_check.py):
- db.verify_write_role(database): benutzt genau einmal database.connection(), führt nur lesende Anweisungen aus
  (SELECT oder WITH), liest die Rolle current_user (nicht session_user) und gibt None zurück oder wirft
  db.RoleCheckError.
- RoleCheckError.failed: Tupel der Kurzbezeichnungen in fester Reihenfolge:
  1 rolle-ist-superuser, 2 rolle-umgeht-rls,
  3 schreibrecht:<RECHT>:<tabelle> für UPDATE, DELETE, TRUNCATE auf customers, contacts, activities (je Tabelle in
    dieser Reihenfolge, auch als Spaltenrecht bei UPDATE),
  4 je Tabelle products, product_fits, discount_rules: lesen-unerlaubt:<tabelle> (SELECT, auch als Spaltenrecht),
    dann schreibrecht:<RECHT>:<tabelle> für INSERT, UPDATE, DELETE, TRUNCATE (INSERT und UPDATE auch als Spaltenrecht),
  5 kein-select:<tabelle> je Schreibtabelle, danach fehlendes-recht:<INSERT>:<tabelle> je Schreibtabelle,
  6 create-im-schema,
  7 tabelle-fehlt:<tabelle> (nur diese, nicht zusätzlich eine andere Kurzbezeichnung zu dieser Tabelle),
  datenbankfehler (Verbindung oder Abfrage scheitert; genau ein WARNING von hoffmann_data.db mit Klasse und SQLSTATE).
- str(error) nennt die Kurzbezeichnungen und sonst nur feste Wörter, nie Rolle, Host, Benutzer, Datenbankname, URL
  oder Passwort. Kein __cause__.

Die Tests mit Datenbank brauchen TEST_DATABASE_URL (nur localhost). Die Rolle aus dem Skript wird in der
Test-Transaktion verändert und mit ihr zurückgerollt. BYPASSRLS und die Superuser-Prüfung brauchen einen
Superuser als Testbenutzer: lokal wird sonst übersprungen, in GitHub Actions ist das ein Fehler.
"""

import logging
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Self

import psycopg
import pytest
from mcp_testkit import DB_SECRETS
from psycopg import errors, sql

DB_LOGGER = "hoffmann_data.db"

# Kurzbezeichnungen (feste Namen; Fallen aus test_package_sql_rules.py: kein eigenes Wort INSERT, UPDATE, DELETE,
# TRUNCATE oder CREATE in einem erzwungenen Literal, kein "select" in einem f-String)
IS_SUPERUSER = "rolle-ist-superuser"
BYPASSES_RLS = "rolle-umgeht-rls"
CREATE_IN_SCHEMA = "create-im-schema"
DATABASE_FAILURE = "datenbankfehler"

INSERT_RIGHT = "INSERT"
WRITE_TABLES = ("customers", "contacts", "activities")
FORBIDDEN_TABLES = ("products", "product_fits", "discount_rules")
ALL_TABLES = (*WRITE_TABLES, *FORBIDDEN_TABLES)
# Unerlaubte Schreibrechte auf den Schreibtabellen (INSERT ist dort erlaubt)
EXTRA_WRITE_RIGHTS = ("UPDATE", "DELETE", "TRUNCATE")
# Auf den Fremdtabellen ist jedes Recht unerlaubt
ALL_WRITE_RIGHTS = (INSERT_RIGHT, *EXTRA_WRITE_RIGHTS)
# Je Tabelle eine Spalte für die Spaltenrechte
COLUMN_OF = {
    "customers": "company_name",
    "contacts": "first_name",
    "activities": "subject",
    "products": "name",
    "product_fits": "note",
    "discount_rules": "description",
}
# Fehlendes SELECT auf einer Schreibtabelle: feste Literale wie NO_SELECT_FAILURES in db.py
NO_SELECT = {
    "customers": "kein-select:customers",
    "contacts": "kein-select:contacts",
    "activities": "kein-select:activities",
}
WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY|MERGE)\b", re.IGNORECASE
)


def forbidden_write(right: str, table: str) -> str:
    return f"schreibrecht:{right}:{table}"


def foreign_read(table: str) -> str:
    return f"lesen-unerlaubt:{table}"


def missing_right(table: str) -> str:
    """Fehlendes INSERT auf einer Schreibtabelle; das Recht kommt aus einer Variablen, nicht aus einem Literal."""
    return f"fehlendes-recht:{INSERT_RIGHT}:{table}"


def table_missing(table: str) -> str:
    return f"tabelle-fehlt:{table}"


# Hilfen (bewusst nicht aus test_role_check.py importiert: Tests importieren einander nicht)


class SuperDatabase:
    """Die Test-Verbindung selbst, ohne SET ROLE: der Testbenutzer."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        with self._conn.transaction():
            yield self._conn


def _statement_text(query: Any) -> str:
    if isinstance(query, str):
        return query
    if isinstance(query, bytes):
        return query.decode("utf-8")
    return str(query.as_string())


class _RecordingCursor:
    def __init__(self, inner: Any, statements: list[str]) -> None:
        self._inner = inner
        self._statements = statements

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._inner.close()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._inner)

    def execute(self, query: Any, *args: Any, **kwargs: Any) -> "_RecordingCursor":
        self._statements.append(_statement_text(query))
        self._inner.execute(query, *args, **kwargs)
        return self

    def executemany(self, query: Any, *args: Any, **kwargs: Any) -> None:
        self._statements.append(_statement_text(query))
        self._inner.executemany(query, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingConnection:
    def __init__(self, inner: Any, statements: list[str]) -> None:
        self._inner = inner
        self._statements = statements

    def execute(self, query: Any, *args: Any, **kwargs: Any) -> Any:
        self._statements.append(_statement_text(query))
        return self._inner.execute(query, *args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> _RecordingCursor:
        return _RecordingCursor(self._inner.cursor(*args, **kwargs), self._statements)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class RecordingDatabase:
    """Umhüllt eine Verbindungsquelle und zeichnet jede ausgeführte SQL-Anweisung auf."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.statements: list[str] = []
        self.opened = 0

    @contextmanager
    def connection(self) -> Iterator[_RecordingConnection]:
        self.opened += 1
        with self._inner.connection() as connection:
            yield _RecordingConnection(connection, self.statements)


class ExplodingConnection:
    """Jeder Zugriff auf die Verbindung (execute, cursor, ...) wirft die übergebene Ausnahme."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __getattr__(self, name: str) -> Any:
        raise self._error


class FakeDatabase:
    """Wirft beim Betreten die Ausnahme oder gibt die übergebene Verbindung heraus."""

    def __init__(self, error_on_enter: BaseException | None = None, connection: Any = None) -> None:
        self._error_on_enter = error_on_enter
        self._connection = connection

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self._error_on_enter is not None:
            raise self._error_on_enter
        yield self._connection


def _error(database: Any) -> Any:
    """Die RoleCheckError der Prüfung; bricht ab, wenn die Prüfung besteht."""
    from hoffmann_data import db

    with pytest.raises(db.RoleCheckError) as info:
        db.verify_write_role(database)
    return info.value


def _grant(conn: Any, privilege: str, table: str, role: str) -> None:
    conn.execute(
        sql.SQL("GRANT {} ON {} TO {}").format(
            sql.SQL(privilege), sql.Identifier(table), sql.Identifier(role)
        )
    )


def _grant_column(conn: Any, privilege: str, table: str, column: str, role: str) -> None:
    conn.execute(
        sql.SQL("GRANT {} ({}) ON {} TO {}").format(
            sql.SQL(privilege), sql.Identifier(column), sql.Identifier(table), sql.Identifier(role)
        )
    )


def _revoke(conn: Any, privilege: str, table: str, role: str) -> None:
    conn.execute(
        sql.SQL("REVOKE {} ON {} FROM {}").format(
            sql.SQL(privilege), sql.Identifier(table), sql.Identifier(role)
        )
    )


def _grant_create(conn: Any, schema: str, role: str) -> None:
    conn.execute(
        sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
            sql.Identifier(schema), sql.Identifier(role)
        )
    )


def _counts(conn: Any) -> list[Any]:
    return [
        conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()
        for t in ALL_TABLES
    ]


def _seed_one_row_per_table(make: Any) -> None:
    customer = make.customer()
    contact = make.contact(customer)
    part = make.product()
    plant = make.product()
    make.fit(part, plant)
    make.activity(customer, contact_id=contact, product_id=part)
    make.rule()


@pytest.fixture
def require_superuser(conn: psycopg.Connection[Any]) -> None:
    """BYPASSRLS vergeben und die Superuser-Prüfung belegen kann nur ein Superuser."""
    row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    if row is not None and row[0]:
        return
    message = "Der Testbenutzer ist kein Superuser: Dieser Fall braucht einen Superuser."
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


def _db_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == DB_LOGGER]


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)


# a) Positiv


def test_the_script_role_passes_all_checks(rw_database: Any) -> None:
    """Die Sitzung gehört dem Testbenutzer (Superuser), geprüft wird die Rolle nach SET LOCAL ROLE."""
    from hoffmann_data import db

    assert db.verify_write_role(rw_database) is None


# b) Negativ je Prüfung


@pytest.mark.parametrize("right", EXTRA_WRITE_RIGHTS)
@pytest.mark.parametrize("table", WRITE_TABLES)
def test_an_extra_write_privilege_is_found(
    conn: Any, rw_role: str, rw_database: Any, table: str, right: str
) -> None:
    _grant(conn, right, table, rw_role)
    error = _error(rw_database)
    assert error.failed == (forbidden_write(right, table),)
    assert forbidden_write(right, table) in str(error)


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_an_extra_column_privilege_is_found(
    conn: Any, rw_role: str, rw_database: Any, table: str
) -> None:
    """Ein Spaltenrecht genügt, um Daten zu ändern; has_table_privilege allein sähe es nicht."""
    _grant_column(conn, "UPDATE", table, COLUMN_OF[table], rw_role)
    error = _error(rw_database)
    assert error.failed == (forbidden_write("UPDATE", table),)


@pytest.mark.parametrize("right", ("SELECT", *ALL_WRITE_RIGHTS))
@pytest.mark.parametrize("table", FORBIDDEN_TABLES)
def test_any_privilege_on_a_forbidden_table_is_found(
    conn: Any, rw_role: str, rw_database: Any, table: str, right: str
) -> None:
    _grant(conn, right, table, rw_role)
    expected = foreign_read(table) if right == "SELECT" else forbidden_write(right, table)
    error = _error(rw_database)
    assert error.failed == (expected,)
    assert expected in str(error)


@pytest.mark.parametrize("right", ("SELECT", "INSERT", "UPDATE"))
@pytest.mark.parametrize("table", FORBIDDEN_TABLES)
def test_a_column_privilege_on_a_forbidden_table_is_found(
    conn: Any, rw_role: str, rw_database: Any, table: str, right: str
) -> None:
    _grant_column(conn, right, table, COLUMN_OF[table], rw_role)
    expected = foreign_read(table) if right == "SELECT" else forbidden_write(right, table)
    error = _error(rw_database)
    assert error.failed == (expected,)


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_a_missing_select_privilege_is_found(
    conn: Any, rw_role: str, rw_database: Any, table: str
) -> None:
    _revoke(conn, "SELECT", table, rw_role)
    error = _error(rw_database)
    assert error.failed == (NO_SELECT[table],)
    assert NO_SELECT[table] in str(error)


@pytest.mark.parametrize("table", WRITE_TABLES)
def test_a_missing_insert_privilege_is_found(
    conn: Any, rw_role: str, rw_database: Any, table: str
) -> None:
    _revoke(conn, INSERT_RIGHT, table, rw_role)
    error = _error(rw_database)
    assert error.failed == (missing_right(table),)
    assert missing_right(table) in str(error)


def test_a_superuser_is_found(conn: Any, require_superuser: None) -> None:
    error = _error(SuperDatabase(conn))
    # Ein Superuser hat jedes Recht, die übrigen Prüfungen scheitern mit; Prüfung 1 steht an erster Stelle
    assert error.failed[0] == IS_SUPERUSER
    assert IS_SUPERUSER in str(error)


def test_a_role_with_bypassrls_is_found(
    conn: Any, rw_role: str, rw_database: Any, require_superuser: None
) -> None:
    conn.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(rw_role)))
    error = _error(rw_database)
    assert error.failed == (BYPASSES_RLS,)
    assert BYPASSES_RLS in str(error)


def test_create_in_the_schema_is_found(
    conn: Any, rw_role: str, rw_database: Any, db_schema: str
) -> None:
    _grant_create(conn, db_schema, rw_role)
    error = _error(rw_database)
    assert error.failed == (CREATE_IN_SCHEMA,)
    assert CREATE_IN_SCHEMA in str(error)


@pytest.mark.parametrize("table", ALL_TABLES)
def test_a_missing_table_is_a_fixed_error_not_a_raw_database_error(
    conn: Any, rw_database: Any, table: str
) -> None:
    """DROP ... CASCADE entfernt nur Fremdschlüssel anderer Tabellen; die Test-Transaktion rollt alles zurück."""
    conn.execute(sql.SQL("DROP TABLE {} CASCADE").format(sql.Identifier(table)))
    error = _error(rw_database)
    assert error.failed == (table_missing(table),), (
        "nur diese Prüfung, keine weitere zu dieser Tabelle"
    )
    assert table_missing(table) in str(error)


def test_several_failures_are_all_reported_in_the_agreed_order(
    conn: Any, rw_role: str, rw_database: Any, db_schema: str
) -> None:
    _grant(conn, "UPDATE", "contacts", rw_role)
    _grant(conn, "DELETE", "customers", rw_role)
    _grant(conn, "SELECT", "products", rw_role)
    _grant(conn, INSERT_RIGHT, "product_fits", rw_role)
    _revoke(conn, "SELECT", "activities", rw_role)
    _revoke(conn, INSERT_RIGHT, "customers", rw_role)
    _grant_create(conn, db_schema, rw_role)
    error = _error(rw_database)
    assert error.failed == (
        forbidden_write("DELETE", "customers"),
        forbidden_write("UPDATE", "contacts"),
        foreign_read("products"),
        forbidden_write(INSERT_RIGHT, "product_fits"),
        NO_SELECT["activities"],
        missing_right("customers"),
        CREATE_IN_SCHEMA,
    )


# c) Keine Geheimnisse


def test_error_log_and_output_contain_no_role_name_or_secret(
    conn: Any,
    rw_role: str,
    rw_database: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    _grant(conn, "UPDATE", "customers", rw_role)
    error = _error(rw_database)
    captured = capsys.readouterr()
    texts = [str(error), repr(error), _all_log_text(caplog), captured.out, captured.err]
    for text in texts:
        assert rw_role not in text
        assert "postgresql://" not in text
        for secret in DB_SECRETS:
            assert secret not in text, secret


def test_the_login_name_of_the_superuser_is_not_in_the_error(
    conn: Any, require_superuser: None
) -> None:
    row = conn.execute("SELECT current_user").fetchone()
    assert row is not None
    login = row[0]
    error = _error(SuperDatabase(conn))
    if len(login) >= 4 and login not in IS_SUPERUSER:
        assert login not in str(error)


# d) Die Prüfung ändert nichts


def test_the_check_changes_no_rows(conn: Any, rw_role: str, rw_database: Any, make: Any) -> None:
    from hoffmann_data import db

    _seed_one_row_per_table(make)
    before = _counts(conn)
    assert db.verify_write_role(rw_database) is None
    assert _counts(conn) == before
    _grant(conn, "UPDATE", "customers", rw_role)
    _error(rw_database)
    assert _counts(conn) == before


def test_the_check_runs_only_read_statements_on_one_connection(rw_database: Any) -> None:
    from hoffmann_data import db

    recording = RecordingDatabase(rw_database)
    assert db.verify_write_role(recording) is None
    assert recording.opened == 1
    assert recording.statements, "die Prüfung hat nichts abgefragt"
    for statement in recording.statements:
        assert re.match(r"\s*(SELECT|WITH)\b", statement, re.IGNORECASE), statement
        # Zeichenketten-Literale (z. B. Rechtenamen wie 'INSERT') dürfen in lesenden SELECTs stehen
        without_literals = re.sub(r"'[^']*'", "''", statement)
        assert not WRITE_WORDS.search(without_literals), statement


# Verbindungs- und Abfragefehler (ohne Datenbank)


@pytest.mark.parametrize(
    ("database", "class_name", "sqlstate"),
    [
        pytest.param(
            lambda: FakeDatabase(
                error_on_enter=psycopg.OperationalError("connection to server at geheimhost failed")
            ),
            "OperationalError",
            None,
            id="verbindung",
        ),
        pytest.param(
            lambda: FakeDatabase(error_on_enter=RuntimeError("geheimtext")),
            "RuntimeError",
            None,
            id="unerwartet-beim-verbinden",
        ),
        pytest.param(
            lambda: FakeDatabase(
                connection=ExplodingConnection(errors.InsufficientPrivilege("geheimtext"))
            ),
            "InsufficientPrivilege",
            "42501",
            id="abfrage-mit-sqlstate",
        ),
        pytest.param(
            lambda: FakeDatabase(connection=ExplodingConnection(RuntimeError("geheimtext"))),
            "RuntimeError",
            None,
            id="unerwartet-beim-abfragen",
        ),
    ],
)
def test_database_failures_are_reported_as_a_fixed_error_and_logged_once(
    caplog: pytest.LogCaptureFixture,
    database: Callable[[], Any],
    class_name: str,
    sqlstate: str | None,
) -> None:
    caplog.set_level(logging.DEBUG)
    error = _error(database())
    assert error.failed == (DATABASE_FAILURE,)
    assert DATABASE_FAILURE in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    records = _db_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == logging.WARNING
    assert class_name in record.getMessage()
    if sqlstate is not None:
        assert sqlstate in record.getMessage()
    assert record.exc_info is None and record.exc_text is None
    for text in (str(error), _all_log_text(caplog)):
        assert "geheim" not in text.lower()


def test_the_error_text_names_no_role_host_or_secret(caplog: pytest.LogCaptureFixture) -> None:
    """Der Text der Ausnahme der Datenbank mit Rolle, Host und Passwort kommt weder in den Fehler noch ins Log."""
    caplog.set_level(logging.DEBUG)
    leak = (
        'connection to server at "geheimhost.example" (10.0.0.7), port 5432 failed: '
        'FATAL: password authentication failed for user "data_service_rw", '
        "postgresql://geheimuser:geheimpasswort@geheimhost.example/geheimdb"
    )
    error = _error(FakeDatabase(error_on_enter=psycopg.OperationalError(leak)))
    for text in (str(error), repr(error), _all_log_text(caplog)):
        for forbidden in (
            "data_service_rw",
            "geheimhost",
            "10.0.0.7",
            "postgresql://",
            *DB_SECRETS,
        ):
            assert forbidden not in text, forbidden
