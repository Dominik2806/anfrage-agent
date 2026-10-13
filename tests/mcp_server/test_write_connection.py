"""Tests des Schreib-Verbindungsmanagers: hoffmann_data/db.py, WriteDatabase und write_connection (F08, ADR 0005 Punkt 8).

Schnittstelle, die diese Tests festlegen (db.py):
- db.WriteDatabase(url, **connect_kwargs): Verbindungsquelle (connection() liefert einen Kontextmanager). Der
  Konstruktor verbindet nicht, repr nennt keine URL. Eigenständig, ohne Code von Database (der Lesepfad bleibt unberührt).
  connection(): psycopg.connect(url, **kwargs) über das Modul psycopg mit connect_timeout=5 und prepare_threshold=None
  (unsere Werte gewinnen gegen übergebene), NICHT schreibgeschützt, als erste Anweisung "SET LOCAL statement_timeout
  = '5s'" (beginnt die Transaktion). Eine Transaktion je Aufruf: Ende des Blocks ohne Ausnahme = connection.commit();
  bei jeder Ausnahme im Block (auch ToolError, auch ein Fehler beim Commit) = connection.rollback(), danach wird die
  ursprüngliche Ausnahme weitergegeben, ein Fehler des Rollbacks verdeckt sie nicht; am Ende immer connection.close().
- db.write_connection(database): Gegenstück zu read_connection. None: ToolError(NOT_CONFIGURED). Ein ToolError im
  Block läuft unverändert durch. Eine UniqueViolation (SQLSTATE 23505): ToolError(DUPLICATE_ERROR). Jede andere
  Ausnahme (auch beim Verbinden, auch Check-, Fremdschlüssel-, NOT-NULL- und Rechte-Verletzung): ToolError(DATABASE_ERROR).
  Kein __cause__, Kontext unterdrückt, genau ein WARNING von hoffmann_data.db mit Klasse und SQLSTATE, nie der Text.
- db.DUPLICATE_ERROR: feste Meldung, verschieden von DATABASE_ERROR und NOT_CONFIGURED.

Die Tests mit Datenbank brauchen TEST_DATABASE_URL (nur localhost:5432). Sie committen echte Zeilen ins Testschema; die
Zeilen tragen den Marker aus mcp_testkit, die Fixture marker_cleanup räumt vor und nach jedem Test auf (nie ohne Marker).
Rot, bis db.py WriteDatabase, write_connection und DUPLICATE_ERROR kennt (TDD).
"""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import (
    DB_SECRETS,
    GOOD_WRITE_URL,
    ROW_MARKER,
    SECRET_MARKER,
    delete_marker_rows,
    marker_row,
)
from psycopg import errors

DB_LOGGER = "hoffmann_data.db"
# Port 1 auf Loopback: Linux lehnt sofort ab (OperationalError), Windows wartet bis connect_timeout=5 (ConnectionTimeout)
UNREACHABLE_URL = "postgresql://geheimuser:geheimpasswort@127.0.0.1:1/geheimdb"
INSERT_CUSTOMER = (
    "INSERT INTO customers (company_name, domain, industry, country, status) "
    "VALUES (%s, %s, 'other', 'de', 'lead')"
)


class RecordingDatabase:
    """Test-Double: gibt ein Objekt heraus und merkt sich, was im Block geschah (Ausnahme oder None bei normalem Ende)."""

    def __init__(self, error_on_enter: BaseException | None = None) -> None:
        self.connection_object = object()
        self.seen: list[BaseException | None] = []
        self._error = error_on_enter

    @contextmanager
    def connection(self) -> Iterator[object]:
        if self._error is not None:
            raise self._error
        try:
            yield self.connection_object
        except BaseException as exc:
            self.seen.append(exc)
            raise
        else:
            self.seen.append(None)


def _db_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == DB_LOGGER]


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)


def _assert_nothing_leaked(
    secrets: tuple[str, ...],
    message: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keiner der Werte steht in Meldung, Log (alle Logger), stdout oder stderr."""
    captured = capsys.readouterr()
    haystacks = {
        "Meldung": message,
        "Log": _all_log_text(caplog),
        "stdout": captured.out,
        "stderr": captured.err,
    }
    for secret in secrets:
        for where, text in haystacks.items():
            assert secret not in text, f"{secret!r} steht in {where}"


ERROR_CASES = [
    pytest.param(
        lambda: errors.UndefinedTable("relation geheimtabelle fehlt"),
        "UndefinedTable",
        "42P01",
        id="psycopg-mit-sqlstate",
    ),
    pytest.param(
        lambda: psycopg.OperationalError("connection to server at geheimhost failed"),
        "OperationalError",
        None,
        id="psycopg-ohne-sqlstate",
    ),
    pytest.param(lambda: RuntimeError("geheimtext"), "RuntimeError", None, id="unerwartet"),
    pytest.param(lambda: KeyError("geheimschluessel"), "KeyError", None, id="unerwartet-keyerror"),
]
# Fehler der Datenbank, die kein Duplikat sind: bleiben die allgemeine Meldung (Entscheidung 5)
OTHER_DATABASE_ERRORS = [
    pytest.param(
        lambda: errors.ForeignKeyViolation("geheim: Schlüssel fehlt"), "23503", id="fremdschluessel"
    ),
    pytest.param(lambda: errors.CheckViolation("geheim: Wert ungültig"), "23514", id="check"),
    pytest.param(lambda: errors.NotNullViolation("geheim: Wert fehlt"), "23502", id="not-null"),
    pytest.param(
        lambda: errors.InsufficientPrivilege("geheim: Recht fehlt"), "42501", id="recht-oder-policy"
    ),
]


# write_connection mit einem Test-Double


def test_constants_are_fixed_distinct_texts() -> None:
    from hoffmann_data import db

    texts = [db.DUPLICATE_ERROR, db.DATABASE_ERROR, db.NOT_CONFIGURED]
    assert all(isinstance(t, str) and t.strip() for t in texts)
    assert len(set(texts)) == len(texts)
    assert all("geheim" not in t.lower() for t in texts)


def test_without_database_write_connection_says_not_configured() -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.write_connection(None):
            pytest.fail("der Block darf ohne Datenbank nicht laufen")
    assert str(info.value) == db.NOT_CONFIGURED


def test_write_connection_gives_the_connection_of_the_database() -> None:
    from hoffmann_data import db

    fake = RecordingDatabase()
    with db.write_connection(fake) as connection:
        assert connection is fake.connection_object
    assert fake.seen == [None]


def test_tool_error_passes_through_unchanged_and_the_database_sees_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ein ToolError des Werkzeugs ist kein Datenbankfehler: nicht umgeschrieben, nicht geloggt.

    Die Datenbank sieht ihn trotzdem (Voraussetzung für den Rollback).
    """
    from hoffmann_data import db

    original = ToolError("feste Meldung des Werkzeugs")
    fake = RecordingDatabase()
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError) as info:
        with db.write_connection(fake):
            raise original
    assert info.value is original
    assert fake.seen == [original]
    assert _db_records(caplog) == []


@pytest.mark.parametrize(("make_error", "class_name", "sqlstate"), ERROR_CASES)
def test_errors_in_the_block_become_the_fixed_database_error(
    make_error: Callable[[], BaseException], class_name: str, sqlstate: str | None
) -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.write_connection(RecordingDatabase()):
            raise make_error()
    assert str(info.value) == db.DATABASE_ERROR
    assert info.value.__cause__ is None
    assert info.value.__context__ is None or info.value.__suppress_context__


@pytest.mark.parametrize(("make_error", "class_name", "sqlstate"), ERROR_CASES)
def test_errors_when_opening_become_the_fixed_database_error(
    make_error: Callable[[], BaseException], class_name: str, sqlstate: str | None
) -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.write_connection(RecordingDatabase(error_on_enter=make_error())):
            pytest.fail("der Block darf nicht laufen, wenn die Verbindung fehlschlägt")
    assert str(info.value) == db.DATABASE_ERROR
    assert info.value.__cause__ is None
    assert info.value.__context__ is None or info.value.__suppress_context__


def test_unique_violation_becomes_the_fixed_duplicate_message() -> None:
    from hoffmann_data import db

    error = errors.UniqueViolation('duplicate key value violates unique constraint "geheimname"')
    with pytest.raises(ToolError) as info:
        with db.write_connection(RecordingDatabase()):
            raise error
    assert str(info.value) == db.DUPLICATE_ERROR
    assert str(info.value) != db.DATABASE_ERROR
    assert info.value.__cause__ is None
    assert info.value.__context__ is None or info.value.__suppress_context__


@pytest.mark.parametrize(("make_error", "sqlstate"), OTHER_DATABASE_ERRORS)
def test_other_integrity_and_privilege_errors_stay_the_generic_database_error(
    make_error: Callable[[], BaseException], sqlstate: str
) -> None:
    from hoffmann_data import db

    error = make_error()
    assert getattr(error, "sqlstate", None) == sqlstate
    with pytest.raises(ToolError) as info:
        with db.write_connection(RecordingDatabase()):
            raise error
    assert str(info.value) == db.DATABASE_ERROR
    assert str(info.value) != db.DUPLICATE_ERROR


@pytest.mark.parametrize(
    ("make_error", "class_name", "sqlstate"),
    [
        *ERROR_CASES,
        pytest.param(
            lambda: errors.UniqueViolation("duplicate key geheimwert"),
            "UniqueViolation",
            "23505",
            id="duplikat",
        ),
    ],
)
def test_exactly_one_warning_with_class_and_sqlstate_only(
    caplog: pytest.LogCaptureFixture,
    make_error: Callable[[], BaseException],
    class_name: str,
    sqlstate: str | None,
) -> None:
    from hoffmann_data import db

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError):
        with db.write_connection(RecordingDatabase()):
            raise make_error()
    records = _db_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert class_name in message
    if sqlstate is not None:
        assert sqlstate in message
    assert "geheim" not in message.lower(), "Text der Ausnahme im Log"
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None
    assert "geheim" not in _all_log_text(caplog).lower()


def test_error_while_opening_logs_one_warning_too(caplog: pytest.LogCaptureFixture) -> None:
    from hoffmann_data import db

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError):
        with db.write_connection(
            RecordingDatabase(error_on_enter=psycopg.OperationalError("geheim"))
        ):
            pytest.fail("der Block darf nicht laufen")
    records = _db_records(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert "geheim" not in records[0].getMessage()


# WriteDatabase mit einer ersetzten psycopg.connect (ohne Datenbank)


class FakeConnection:
    """Zeichnet auf, was WriteDatabase mit der Verbindung macht. `fail` nennt Schritte, die eine Ausnahme werfen."""

    def __init__(self, fail: dict[str, BaseException]) -> None:
        self.events: list[str] = []
        self.statements: list[str] = []
        self.closed = False
        self._fail = fail
        self._read_only: bool | None = None

    @property
    def read_only(self) -> bool | None:
        return self._read_only

    @read_only.setter
    def read_only(self, value: bool | None) -> None:
        self._read_only = value
        # Nur "schreibgeschützt" ist ein Ereignis; ein ausdrückliches read_only = False ändert nichts am Ablauf
        if value:
            self.events.append("read_only=True")

    def _step(self, name: str) -> None:
        self.events.append(name)
        if name in self._fail:
            raise self._fail[name]

    def execute(self, query: Any, *args: Any, **kwargs: Any) -> "FakeConnection":
        self.statements.append(str(query))
        self._step("execute")
        return self

    def commit(self) -> None:
        self._step("commit")

    def rollback(self) -> None:
        self._step("rollback")

    def close(self) -> None:
        self.closed = True
        self._step("close")


class FakeConnect:
    """Ersatz für psycopg.connect: merkt sich Aufrufe und gibt FakeConnection heraus."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.connections: list[FakeConnection] = []
        self.fail: dict[str, BaseException] = {}
        self.connect_error: BaseException | None = None


@pytest.fixture
def fake_connect(monkeypatch: pytest.MonkeyPatch) -> FakeConnect:
    fake = FakeConnect()

    def connect(*args: Any, **kwargs: Any) -> FakeConnection:
        fake.calls.append((args, kwargs))
        if fake.connect_error is not None:
            raise fake.connect_error
        connection = FakeConnection(dict(fake.fail))
        fake.connections.append(connection)
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    return fake


def test_constructor_does_not_connect_and_repr_hides_the_url(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    database = WriteDatabase(UNREACHABLE_URL)
    assert fake_connect.calls == []
    for text in (repr(database), str(database)):
        for secret in DB_SECRETS:
            assert secret not in text
        assert "127.0.0.1" not in text


def test_connect_options(fake_connect: FakeConnect) -> None:
    """Unsere Werte stehen zuletzt und gewinnen gegen übergebene (wie bei Database); sonstige Optionen bleiben."""
    from hoffmann_data.db import WriteDatabase

    database = WriteDatabase(
        GOOD_WRITE_URL, options="-csearch_path=x", connect_timeout=99, prepare_threshold=0
    )
    with database.connection():
        pass
    ((args, kwargs),) = fake_connect.calls
    assert args == (GOOD_WRITE_URL,)
    assert kwargs["connect_timeout"] == 5
    assert "prepare_threshold" in kwargs and kwargs["prepare_threshold"] is None
    assert kwargs["options"] == "-csearch_path=x"


def test_the_timeout_runs_first_and_the_connection_is_not_read_only(
    fake_connect: FakeConnect,
) -> None:
    from hoffmann_data.db import WriteDatabase

    with WriteDatabase(GOOD_WRITE_URL).connection() as connection:
        assert connection.events == ["execute"], "die erste Anweisung läuft vor dem Block"
        assert "statement_timeout" in connection.statements[0]
        assert "5s" in connection.statements[0]
    assert fake_connect.connections[0].read_only is not True
    assert "read_only=True" not in fake_connect.connections[0].events


def test_commit_at_the_end_then_close(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    with WriteDatabase(GOOD_WRITE_URL).connection():
        pass
    assert fake_connect.connections[0].events == ["execute", "commit", "close"]


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(lambda: RuntimeError("Abbruch"), id="laufzeitfehler"),
        pytest.param(lambda: ToolError("Fehler des Werkzeugs"), id="werkzeugfehler"),
        pytest.param(lambda: errors.UniqueViolation("doppelt"), id="duplikat"),
    ],
)
def test_rollback_on_any_error_in_the_block(
    fake_connect: FakeConnect, make_error: Callable[[], BaseException]
) -> None:
    from hoffmann_data.db import WriteDatabase

    original = make_error()
    with pytest.raises(type(original)) as info:
        with WriteDatabase(GOOD_WRITE_URL).connection():
            raise original
    assert info.value is original
    assert fake_connect.connections[0].events == ["execute", "rollback", "close"]


def test_a_keyboard_interrupt_in_the_block_is_rolled_back_and_passed_on(
    fake_connect: FakeConnect,
) -> None:
    """Auch ein Abbruch durch den Benutzer (BaseException, keine Exception) rollt zurück, schließt und
    läuft unverändert nach außen."""
    from hoffmann_data.db import WriteDatabase

    original = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as info:
        with WriteDatabase(GOOD_WRITE_URL).connection():
            raise original
    assert info.value is original
    connection = fake_connect.connections[0]
    assert connection.events == ["execute", "rollback", "close"], (
        "kein Commit, erst Rollback, dann Schließen"
    )
    assert connection.closed


def test_an_error_on_commit_rolls_back_and_closes(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    fake_connect.fail = {"commit": psycopg.OperationalError("Commit gescheitert")}
    with pytest.raises(psycopg.OperationalError):
        with WriteDatabase(GOOD_WRITE_URL).connection():
            pass
    assert fake_connect.connections[0].events == ["execute", "commit", "rollback", "close"]


def test_a_failing_rollback_does_not_hide_the_original_error(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    fake_connect.fail = {"rollback": psycopg.OperationalError("Rollback gescheitert")}
    original = RuntimeError("ursprünglicher Fehler")
    with pytest.raises(RuntimeError) as info:
        with WriteDatabase(GOOD_WRITE_URL).connection():
            raise original
    assert info.value is original
    connection = fake_connect.connections[0]
    assert connection.closed and connection.events[-1] == "close"


def test_a_failing_timeout_statement_closes_the_connection(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    fake_connect.fail = {"execute": psycopg.OperationalError("Zeitlimit nicht gesetzt")}
    ran: list[bool] = []
    with pytest.raises(psycopg.OperationalError):
        with WriteDatabase(GOOD_WRITE_URL).connection():
            ran.append(True)
    assert ran == [], "der Block darf ohne gesetztes Zeitlimit nicht laufen"
    connection = fake_connect.connections[0]
    assert connection.closed
    assert "commit" not in connection.events


def test_a_failing_connect_propagates_and_leaves_nothing_open(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    fake_connect.connect_error = psycopg.OperationalError("nicht erreichbar")
    with pytest.raises(psycopg.OperationalError):
        with WriteDatabase(GOOD_WRITE_URL).connection():
            pytest.fail("der Block darf nicht laufen")
    assert fake_connect.connections == []


def test_every_call_opens_a_new_connection_and_closes_it(fake_connect: FakeConnect) -> None:
    from hoffmann_data.db import WriteDatabase

    database = WriteDatabase(GOOD_WRITE_URL)
    with database.connection():
        pass
    with pytest.raises(RuntimeError):
        with database.connection():
            raise RuntimeError("Abbruch")
    first, second = fake_connect.connections
    assert first is not second
    assert first.closed and second.closed
    assert len(fake_connect.calls) == 2


def test_write_connection_rolls_back_a_tool_error(fake_connect: FakeConnect) -> None:
    """Werkzeugfehler (ToolError) im Block über write_connection: Rollback, der Fehler bleibt unverändert."""
    from hoffmann_data import db

    original = ToolError("feste Meldung des Werkzeugs")
    with pytest.raises(ToolError) as info:
        with db.write_connection(db.WriteDatabase(GOOD_WRITE_URL)):
            raise original
    assert info.value is original
    assert fake_connect.connections[0].events == ["execute", "rollback", "close"]


def test_write_connection_rolls_back_before_it_maps_a_duplicate(fake_connect: FakeConnect) -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.write_connection(db.WriteDatabase(GOOD_WRITE_URL)):
            raise errors.UniqueViolation("doppelt")
    assert str(info.value) == db.DUPLICATE_ERROR
    assert fake_connect.connections[0].events == ["execute", "rollback", "close"]


def test_write_connection_does_not_map_a_keyboard_interrupt(
    fake_connect: FakeConnect, caplog: pytest.LogCaptureFixture
) -> None:
    """Ein KeyboardInterrupt ist kein Datenbankfehler: kein ToolError (weder DATABASE_ERROR noch DUPLICATE_ERROR),
    kein Log."""
    from hoffmann_data import db

    caplog.set_level(logging.DEBUG)
    original = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as info:
        with db.write_connection(db.WriteDatabase(GOOD_WRITE_URL)):
            raise original
    assert info.value is original
    assert fake_connect.connections[0].events == ["execute", "rollback", "close"]
    assert _db_records(caplog) == []


def test_unreachable_database_gives_the_fixed_error_and_leaks_nothing(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Die echte WriteDatabase gegen einen unerreichbaren Port (Linux: OperationalError, Windows: ConnectionTimeout)."""
    from hoffmann_data import db

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError) as info:
        with db.write_connection(db.WriteDatabase(UNREACHABLE_URL)):
            pytest.fail("der Block darf nicht laufen")
    assert str(info.value) == db.DATABASE_ERROR
    assert info.value.__cause__ is None
    records = _db_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert re.search(r"[A-Za-z]+(Error|Timeout)", records[0].getMessage())
    _assert_nothing_leaked(DB_SECRETS, str(info.value), caplog, capsys)


# Die echte WriteDatabase gegen den Test-Server (TEST_DATABASE_URL)


def _insert(connection: Any, row: dict[str, str], company_name: str | None = None) -> None:
    connection.execute(INSERT_CUSTOMER, (company_name or row["company_name"], row["domain"]))


def test_commit_at_the_end_is_visible_to_another_connection(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    row = marker_row()
    with real_write_database.connection() as connection:
        _insert(connection, row)
        assert committed_customers(row["domain"]) == 0, (
            "vor dem Ende des Blocks ist nichts committet"
        )
    assert committed_customers(row["domain"]) == 1


def test_rollback_on_an_error_in_the_block(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    row = marker_row()
    with pytest.raises(RuntimeError):
        with real_write_database.connection() as connection:
            _insert(connection, row)
            raise RuntimeError("Abbruch im Block")
    assert committed_customers(row["domain"]) == 0
    assert connection.closed


def test_a_keyboard_interrupt_stores_nothing_in_the_real_database(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    """Ergänzung zu den Tests mit der Fake-Verbindung: Nichts wird committet. Den Rollback selbst belegen jene Tests;
    der Server würde eine offene Transaktion beim Schließen ohnehin zurückrollen."""
    row = marker_row()
    with pytest.raises(KeyboardInterrupt):
        with real_write_database.connection() as connection:
            _insert(connection, row)
            raise KeyboardInterrupt
    assert committed_customers(row["domain"]) == 0
    assert connection.closed


def test_rollback_on_a_tool_error(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    from hoffmann_data import db

    row = marker_row()
    original = ToolError("feste Meldung des Werkzeugs")
    with pytest.raises(ToolError) as info:
        with db.write_connection(real_write_database) as connection:
            _insert(connection, row)
            raise original
    assert info.value is original
    assert committed_customers(row["domain"]) == 0


def test_a_second_failing_insert_rolls_back_the_first(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    from hoffmann_data import db

    existing = marker_row()
    with db.write_connection(real_write_database) as connection:
        _insert(connection, existing)
    assert committed_customers(existing["domain"]) == 1

    new = marker_row()
    with pytest.raises(ToolError) as info:
        with db.write_connection(real_write_database) as connection:
            _insert(connection, new)
            _insert(connection, existing)  # gleiche Domain: UNIQUE schlägt zu
    assert str(info.value) == db.DUPLICATE_ERROR
    assert committed_customers(new["domain"]) == 0, "der erste INSERT wurde zurückgerollt"
    assert committed_customers(existing["domain"]) == 1


def test_a_duplicate_company_name_is_a_duplicate_error_too(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    """Der zweite UNIQUE-Index: Firmenname ohne Beachtung der Schreibung."""
    from hoffmann_data import db

    existing = marker_row()
    with db.write_connection(real_write_database) as connection:
        _insert(connection, existing)
    other = marker_row()
    with pytest.raises(ToolError) as info:
        with db.write_connection(real_write_database) as connection:
            _insert(connection, other, company_name=existing["company_name"].upper())
    assert str(info.value) == db.DUPLICATE_ERROR
    assert committed_customers(other["domain"]) == 0


def test_the_connection_is_not_read_only(
    real_write_database: Any, marker_cleanup: None, committed_customers: Callable[[str], int]
) -> None:
    row = marker_row()
    with real_write_database.connection() as connection:
        assert connection.read_only is not True
        assert connection.execute("SHOW transaction_read_only").fetchone() == ("off",)
        _insert(connection, row)
    assert committed_customers(row["domain"]) == 1


def test_the_connection_has_a_five_second_statement_timeout(real_write_database: Any) -> None:
    with real_write_database.connection() as connection:
        assert connection.execute("SHOW statement_timeout").fetchone() == ("5s",)


def test_connection_does_not_use_prepared_statements_and_has_a_connect_timeout(
    real_write_database: Any,
) -> None:
    with real_write_database.connection() as connection:
        assert connection.prepare_threshold is None
        assert connection.info.get_parameters().get("connect_timeout") == "5"


def test_every_call_opens_and_closes_its_own_connection(
    real_write_database: Any, marker_cleanup: None, connect_log: Any
) -> None:
    before = connect_log.calls
    with real_write_database.connection() as first:
        first.execute("SELECT 1")
    with real_write_database.connection() as second:
        second.execute("SELECT 1")
    with pytest.raises(RuntimeError):
        with real_write_database.connection():
            raise RuntimeError("Abbruch im Block")
    assert connect_log.calls - before == 3
    opened = connect_log.opened[-3:]
    assert len({id(c) for c in opened}) == 3
    assert all(connection.closed for connection in opened)


@pytest.mark.parametrize("case", ["duplikat", "check"])
def test_error_texts_contain_no_input_value_host_user_or_url(
    real_write_database: Any,
    marker_cleanup: None,
    db_url: str,
    case: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Die Datenbank nennt in ihrem Fehlertext die Werte der Zeile (DETAIL). Nichts davon darf nach außen."""
    from hoffmann_data import db

    row = marker_row()
    row["company_name"] = f"{ROW_MARKER} {SECRET_MARKER} {row['domain']}"
    with db.write_connection(real_write_database) as connection:
        _insert(connection, row)
    parts = urlsplit(db_url)
    secrets = tuple(
        s
        for s in (SECRET_MARKER, row["domain"], parts.username, parts.password, parts.hostname)
        if s and len(s) >= 4
    ) + (db_url,)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError) as info:
        with db.write_connection(real_write_database) as connection:
            if case == "duplikat":
                _insert(connection, row)
            else:
                connection.execute(
                    "INSERT INTO customers (company_name, domain, industry, country, status) "
                    "VALUES (%s, %s, %s, 'de', 'lead')",
                    (f"{ROW_MARKER} anders {SECRET_MARKER}", marker_row()["domain"], SECRET_MARKER),
                )
    expected = db.DUPLICATE_ERROR if case == "duplikat" else db.DATABASE_ERROR
    assert str(info.value) == expected
    assert len(_db_records(caplog)) == 1
    _assert_nothing_leaked(secrets, json.dumps(str(info.value)), caplog, capsys)


def test_the_cleanup_deletes_marker_rows_only(
    db_url: str,
    db_schema: str,
    real_write_database: Any,
    marker_cleanup: None,
    committed_customers: Callable[[str], int],
) -> None:
    """Die Aufräumhilfe löscht Kunde, Kontakt und Aktivität mit Marker und nie eine Zeile ohne Marker."""
    marked = marker_row()
    unmarked_domain = f"ohne-marker-{marked['domain']}"
    with psycopg.connect(db_url, autocommit=True, options=f"-csearch_path={db_schema}") as admin:
        try:
            with real_write_database.connection() as connection:
                _insert(connection, marked)
                connection.execute(
                    INSERT_CUSTOMER, ("Ohne Marker " + marked["domain"], unmarked_domain)
                )
                customer = connection.execute(
                    "SELECT id FROM customers WHERE domain = %s", (marked["domain"],)
                ).fetchone()
                assert customer is not None
                connection.execute(
                    "INSERT INTO contacts (customer_id, first_name, last_name, email, language) "
                    "VALUES (%s, 'Erika', 'Muster', %s, 'de')",
                    (customer[0], marked["email"]),
                )
                connection.execute(
                    "INSERT INTO activities (customer_id, type, occurred_at, subject, summary, created_by) "
                    "VALUES (%s, 'note', now(), 'Betreff', 'Zusammenfassung', 'agent')",
                    (customer[0],),
                )
            assert committed_customers(marked["domain"]) == 1
            assert committed_customers(unmarked_domain) == 1

            for pattern in ("%", "firma-%", "mcp-schreibtest%", ""):
                with pytest.raises(ValueError):
                    delete_marker_rows(admin, pattern)
            assert committed_customers(marked["domain"]) == 1, (
                "ein abgelehntes Muster löscht nichts"
            )

            delete_marker_rows(admin)
            assert committed_customers(marked["domain"]) == 0
            assert committed_customers(unmarked_domain) == 1, "ohne Marker bleibt die Zeile stehen"
            leftovers = admin.execute(
                "SELECT (SELECT count(*) FROM contacts WHERE email = %s), "
                "(SELECT count(*) FROM activities WHERE customer_id = %s)",
                (marked["email"], customer[0]),
            ).fetchone()
            assert leftovers == (0, 0)
        finally:
            admin.execute("DELETE FROM customers WHERE domain = %s", (unmarked_domain,))
