"""Tests der Startprüfung der Datenbankrolle: hoffmann_data/db.py und main() (F07, Etappe 7).

Beim Start prüft der Datenservice, dass die Rolle der Verbindung (MCP_SERVER_DATABASE_URL) wirklich nur
lesen darf (Auftrag 5.1). Eine falsche Rolle verhindert den Start; es gibt keinen Schalter, der die Prüfung
abschaltet. Die Datenbank bleibt die eigentliche Sperre (db/roles/data_service_ro.sql); die Prüfung ist eine
Momentaufnahme beim Start.

Schnittstelle, die diese Tests festlegen:
- db.verify_read_only_role(database): benutzt genau einmal database.connection(), führt nur lesende
  Anweisungen aus (SELECT oder WITH), liest die Rolle current_user (nicht session_user) und gibt None zurück
  oder wirft db.RoleCheckError.
- db.RoleCheckError(failed): Attribut `failed` (Tupel der Kurzbezeichnungen in fester Reihenfolge: Prüfung 1
  bis 6, innerhalb von 3 die Tabellen in der Reihenfolge products, product_fits, customers, contacts, activities).
  str(error) nennt die Kurzbezeichnungen und sonst nur feste Wörter, nie Rollenname, Host, Benutzer,
  Datenbankname, URL oder Passwort. Kein __cause__.
- Kurzbezeichnungen: rolle-ist-superuser, rolle-umgeht-rls, schreibrecht:<RECHT>:<tabelle> (INSERT, UPDATE,
  DELETE, TRUNCATE; INSERT und UPDATE auch als Spaltenrecht), kein-select:<tabelle>, create-im-schema,
  tabelle-fehlt:<tabelle> (nur diese, nicht zusätzlich kein-select oder schreibrecht) und datenbankfehler
  (Verbindung oder Abfrage scheitert; genau ein WARNING von hoffmann_data.db mit Klasse und SQLSTATE).
- main() ruft db.verify_read_only_role(db.Database(url)) über das Modul db auf, bevor uvicorn.run startet.
  Scheitert die Prüfung: Exit-Code 1, die Kurzbezeichnungen auf stderr, uvicorn.run wird nicht aufgerufen.
  Ohne URL bleibt es beim Abbruch von F07 (die Prüfung wird nicht aufgerufen).

Die Tests mit Datenbank brauchen TEST_DATABASE_URL (nur localhost). Die Rolle aus dem Skript wird in der
Test-Transaktion verändert (GRANT, REVOKE, ALTER, DROP) und mit ihr zurückgerollt. BYPASSRLS und die
Superuser-Prüfung brauchen einen Superuser als Testbenutzer: lokal wird sonst übersprungen, in GitHub
Actions ist das ein Fehler.
"""

import ast
import logging
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from mcp_testkit import DB_SECRETS, MCP_SERVER_DIR, READABLE_TABLES
from psycopg import errors, sql

NAME = "MCP_SERVER_DATABASE_URL"
GOOD_URL = "postgresql://geheimuser:geheimpasswort@localhost:5432/geheimdb"
# Port 1 auf Loopback: Linux lehnt die Verbindung sofort ab (OperationalError). Windows wartet bis zum
# Zeitlimit connect_timeout=5 und psycopg wirft ConnectionTimeout (der Test dauert dort etwa 5,5 Sekunden).
UNREACHABLE_URL = "postgresql://geheimuser:geheimpasswort@127.0.0.1:1/geheimdb"
DB_LOGGER = "hoffmann_data.db"

IS_SUPERUSER = "rolle-ist-superuser"
BYPASSES_RLS = "rolle-umgeht-rls"
CREATE_IN_SCHEMA = "create-im-schema"
DATABASE_FAILURE = "datenbankfehler"
WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
# Je Tabelle eine Spalte für die Spaltenrechte
COLUMN_OF = {
    "products": "name",
    "product_fits": "note",
    "customers": "company_name",
    "contacts": "first_name",
    "activities": "subject",
}
WRITE_WORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY|MERGE)\b", re.IGNORECASE
)


def write_right(privilege: str, table: str) -> str:
    return f"schreibrecht:{privilege}:{table}"


def no_select(table: str) -> str:
    return f"kein-select:{table}"


def table_missing(table: str) -> str:
    return f"tabelle-fehlt:{table}"


# Hilfen


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

    def __enter__(self) -> "_RecordingCursor":
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


class ReadOnlySpy:
    """Umhüllt die echte Database und merkt sich, ob die Verbindung schreibgeschützt war und geschlossen wurde."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.read_only: list[bool] = []
        self.connections: list[Any] = []

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._inner.connection() as connection:
            self.read_only.append(connection.read_only is True)
            self.connections.append(connection)
            yield connection


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
        db.verify_read_only_role(database)
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


def _counts(conn: Any) -> list[Any]:
    return [
        conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()
        for t in READABLE_TABLES
    ]


def _seed_one_row_per_table(make: Any) -> None:
    customer = make.customer()
    contact = make.contact(customer)
    part = make.product()
    plant = make.product()
    make.fit(part, plant)
    make.activity(customer, contact_id=contact, product_id=part)


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


# RoleCheckError ohne Datenbank


def test_role_check_error_keeps_the_failed_checks_in_order() -> None:
    from hoffmann_data import db

    error = db.RoleCheckError(["rolle-ist-superuser", "kein-select:contacts"])
    assert isinstance(error, Exception)
    assert error.failed == ("rolle-ist-superuser", "kein-select:contacts")
    assert "rolle-ist-superuser" in str(error) and "kein-select:contacts" in str(error)
    assert error.__cause__ is None


# a) Positiv


def test_the_script_role_passes_all_checks(ro_database: Any) -> None:
    """Die Sitzung gehört dem Testbenutzer (Superuser), geprüft wird die Rolle nach SET LOCAL ROLE."""
    from hoffmann_data import db

    assert db.verify_read_only_role(ro_database) is None


# b) Negativ je Prüfung


def test_a_superuser_is_found(conn: Any, require_superuser: None) -> None:
    error = _error(SuperDatabase(conn))
    # Ein Superuser hat jedes Recht: 3 und 5 scheitern mit; die Reihenfolge stellt Prüfung 1 voran
    assert error.failed[0] == IS_SUPERUSER
    assert IS_SUPERUSER in str(error)


def test_a_role_with_bypassrls_is_found(
    conn: Any, ro_role: str, ro_database: Any, require_superuser: None
) -> None:
    conn.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(ro_role)))
    error = _error(ro_database)
    assert error.failed == (BYPASSES_RLS,)
    assert BYPASSES_RLS in str(error)


@pytest.mark.parametrize("privilege", WRITE_PRIVILEGES)
@pytest.mark.parametrize("table", READABLE_TABLES)
def test_a_table_write_privilege_is_found(
    conn: Any, ro_role: str, ro_database: Any, table: str, privilege: str
) -> None:
    _grant(conn, privilege, table, ro_role)
    error = _error(ro_database)
    assert error.failed == (write_right(privilege, table),)
    assert write_right(privilege, table) in str(error)


@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE"])
@pytest.mark.parametrize("table", READABLE_TABLES)
def test_a_column_write_privilege_is_found(
    conn: Any, ro_role: str, ro_database: Any, table: str, privilege: str
) -> None:
    """Ein Spaltenrecht genügt, um Daten zu ändern; has_table_privilege allein sähe es nicht."""
    _grant_column(conn, privilege, table, COLUMN_OF[table], ro_role)
    error = _error(ro_database)
    assert error.failed == (write_right(privilege, table),)


@pytest.mark.parametrize("table", READABLE_TABLES)
def test_a_missing_select_privilege_is_found(
    conn: Any, ro_role: str, ro_database: Any, table: str
) -> None:
    conn.execute(
        sql.SQL("REVOKE SELECT ON {} FROM {}").format(
            sql.Identifier(table), sql.Identifier(ro_role)
        )
    )
    error = _error(ro_database)
    assert error.failed == (no_select(table),)
    assert no_select(table) in str(error)


def test_create_in_the_schema_is_found(
    conn: Any, ro_role: str, ro_database: Any, db_schema: str
) -> None:
    conn.execute(
        sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
            sql.Identifier(db_schema), sql.Identifier(ro_role)
        )
    )
    error = _error(ro_database)
    assert error.failed == (CREATE_IN_SCHEMA,)
    assert CREATE_IN_SCHEMA in str(error)


@pytest.mark.parametrize("table", READABLE_TABLES)
def test_a_missing_table_is_a_fixed_error_not_a_raw_database_error(
    conn: Any, ro_database: Any, table: str
) -> None:
    """DROP ... CASCADE entfernt nur Fremdschlüssel anderer Tabellen; die Test-Transaktion rollt alles zurück."""
    conn.execute(sql.SQL("DROP TABLE {} CASCADE").format(sql.Identifier(table)))
    error = _error(ro_database)
    assert error.failed == (table_missing(table),), "nur diese Prüfung, nicht zusätzlich 3 oder 4"
    assert table_missing(table) in str(error)


def test_several_failures_are_all_reported_in_the_agreed_order(
    conn: Any, ro_role: str, ro_database: Any, db_schema: str
) -> None:
    _grant(conn, "INSERT", "products", ro_role)
    _grant(conn, "UPDATE", "customers", ro_role)
    conn.execute(sql.SQL("REVOKE SELECT ON contacts FROM {}").format(sql.Identifier(ro_role)))
    conn.execute(
        sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
            sql.Identifier(db_schema), sql.Identifier(ro_role)
        )
    )
    error = _error(ro_database)
    assert error.failed == (
        write_right("INSERT", "products"),
        write_right("UPDATE", "customers"),
        no_select("contacts"),
        CREATE_IN_SCHEMA,
    )


# c) Keine Geheimnisse


def test_error_log_and_output_contain_no_role_name_or_secret(
    conn: Any,
    ro_role: str,
    ro_database: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    _grant(conn, "INSERT", "products", ro_role)
    error = _error(ro_database)
    captured = capsys.readouterr()
    texts = [str(error), repr(error), _all_log_text(caplog), captured.out, captured.err]
    for text in texts:
        assert ro_role not in text
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


def test_the_check_changes_no_rows(conn: Any, ro_role: str, ro_database: Any, make: Any) -> None:
    from hoffmann_data import db

    _seed_one_row_per_table(make)
    before = _counts(conn)
    assert db.verify_read_only_role(ro_database) is None
    assert _counts(conn) == before
    _grant(conn, "INSERT", "products", ro_role)
    _error(ro_database)
    assert _counts(conn) == before


def test_the_check_runs_only_read_statements_on_one_connection(ro_database: Any) -> None:
    from hoffmann_data import db

    recording = RecordingDatabase(ro_database)
    assert db.verify_read_only_role(recording) is None
    assert recording.opened == 1
    assert recording.statements, "die Prüfung hat nichts abgefragt"
    for statement in recording.statements:
        assert re.match(r"\s*(SELECT|WITH)\b", statement, re.IGNORECASE), statement
        # Zeichenketten-Literale (z. B. Rechtenamen wie 'INSERT') dürfen in lesenden SELECTs stehen
        without_literals = re.sub(r"'[^']*'", "''", statement)
        assert not WRITE_WORDS.search(without_literals), statement


def test_the_check_uses_one_read_only_connection_and_closes_it(real_database: Any) -> None:
    """Mit der echten Database: Der Testbenutzer ist kein Nur-Lese-Benutzer, die Prüfung scheitert erwartbar."""
    spy = ReadOnlySpy(real_database)
    _error(spy)
    assert spy.read_only == [True]
    assert len(spy.connections) == 1 and spy.connections[0].closed


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


# e) main()


def _control_main(
    monkeypatch: pytest.MonkeyPatch, failure: Exception | None = None
) -> tuple[list[str], list[Any], list[dict[str, Any]]]:
    """Ersetzt die Prüfung und uvicorn.run; gibt Ereignisse, Prüfungsaufrufe und uvicorn-Aufrufe zurück."""
    import uvicorn

    from hoffmann_data import db

    events: list[str] = []
    verify_calls: list[Any] = []
    run_calls: list[dict[str, Any]] = []

    def fake_verify(database: Any) -> None:
        events.append("verify")
        verify_calls.append(database)
        if failure is not None:
            raise failure

    def fake_run(app: Any, **kwargs: Any) -> None:
        events.append("run")
        run_calls.append(kwargs)

    monkeypatch.setattr(db, "verify_read_only_role", fake_verify)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    return events, verify_calls, run_calls


def test_start_stops_when_the_role_check_fails(
    token: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    failure = db.RoleCheckError((IS_SUPERUSER, write_right("INSERT", "products")))
    events, verify_calls, run_calls = _control_main(monkeypatch, failure)
    with pytest.raises(SystemExit) as info:
        main({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL})
    assert info.value.code == 1
    assert events == ["verify"], "uvicorn.run darf nach einer gescheiterten Prüfung nicht laufen"
    assert run_calls == []
    assert len(verify_calls) == 1 and isinstance(verify_calls[0], db.Database)
    captured = capsys.readouterr()
    assert IS_SUPERUSER in captured.err and write_right("INSERT", "products") in captured.err
    output = captured.out + captured.err
    assert GOOD_URL not in output and token not in output
    for secret in DB_SECRETS:
        assert secret not in output, secret


def test_start_continues_when_the_role_check_passes(
    token: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    events, verify_calls, run_calls = _control_main(monkeypatch)
    main({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL})
    assert events == ["verify", "run"], "die Prüfung läuft vor uvicorn.run, je genau einmal"
    assert len(verify_calls) == 1
    assert run_calls[0]["host"] == "127.0.0.1" and run_calls[0]["access_log"] is False
    captured = capsys.readouterr()
    assert GOOD_URL not in captured.out + captured.err


def test_start_without_url_stops_and_does_not_run_the_check(
    token: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    events, _, run_calls = _control_main(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main({"MCP_SERVER_TOKEN": token})
    assert info.value.code == 1
    assert NAME in capsys.readouterr().err
    assert events == [] and run_calls == []


# f) Kein Abschalter

ENV_NAME_LIKE = re.compile(r"[A-Z][A-Z0-9_]{3,}")
SWITCH_FRAGMENTS = ("SKIP", "DISABLE", "NO_CHECK", "UNSAFE")
EXPECTED_MCP_VARIABLES = {
    "MCP_SERVER_TOKEN",
    "MCP_SERVER_HOST",
    "MCP_SERVER_PORT",
    "MCP_SERVER_ALLOWED_HOSTS",
    "MCP_SERVER_DATABASE_URL",
}


def test_no_switch_variable_exists_in_the_package() -> None:
    """Keine Umgebungsvariable in config.py, __main__.py oder db.py kann die Prüfung abschalten (AST)."""
    package = Path(MCP_SERVER_DIR) / "hoffmann_data"
    names: set[str] = set()
    for filename in ("config.py", "__main__.py", "db.py"):
        tree = ast.parse((package / filename).read_text(encoding="utf-8"), filename=filename)
        names |= {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and ENV_NAME_LIKE.fullmatch(node.value)
        }
    offenders = sorted(n for n in names if any(f in n for f in SWITCH_FRAGMENTS))
    assert not offenders, f"mögliche Abschalter: {offenders}"
    assert {n for n in names if n.startswith("MCP_")} == EXPECTED_MCP_VARIABLES


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("MCP_SERVER_SKIP_ROLE_CHECK", "1"),
        ("MCP_SERVER_DISABLE_ROLE_CHECK", "true"),
        ("MCP_SERVER_UNSAFE", "1"),
        ("MCP_SERVER_NO_CHECK", "true"),
        ("SKIP_ROLE_CHECK", "1"),
        ("DISABLE_ROLE_CHECK", "true"),
    ],
)
def test_such_a_variable_changes_nothing(
    token: str, variable: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    failure = db.RoleCheckError((IS_SUPERUSER,))
    _, verify_calls, run_calls = _control_main(monkeypatch, failure)
    with pytest.raises(SystemExit) as info:
        main({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL, variable: value})
    assert info.value.code == 1
    assert len(verify_calls) == 1 and run_calls == []


# g) Unerreichbare Datenbank beim Start


def test_unreachable_database_stops_the_start_with_a_fixed_message(
    token: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Echte Prüfung, nur uvicorn.run ist ersetzt. Plattformunabhängig: Linux OperationalError, Windows
    ConnectionTimeout nach etwa 5 Sekunden; der Test nennt keine einzelne Klasse."""
    import uvicorn

    from hoffmann_data.__main__ import main

    run_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: run_calls.append(kwargs))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SystemExit) as info:
        main({"MCP_SERVER_TOKEN": token, NAME: UNREACHABLE_URL})
    assert info.value.code == 1
    assert run_calls == []
    captured = capsys.readouterr()
    assert DATABASE_FAILURE in captured.err
    texts = {
        "stdout": captured.out,
        "stderr": captured.err,
        "Log": _all_log_text(caplog),
    }
    for where, text in texts.items():
        assert UNREACHABLE_URL not in text, where
        assert token not in text, where
        for secret in DB_SECRETS:
            assert secret not in text, f"{secret!r} steht in {where}"
    records = _db_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert re.search(r"[A-Za-z]+(Error|Timeout)", records[0].getMessage())
