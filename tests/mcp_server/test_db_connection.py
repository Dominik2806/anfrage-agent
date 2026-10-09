"""Tests der Datenbankverbindung des Datenservice: hoffmann_data/db.py (F07).

Ohne Datenbank laufen die Tests von read_connection (Fehlerabbildung, Log), der Fehlerfälle über die ganze
App (unerreichbarer Port, kein Eintrag in der Konfiguration) und die Prüfungen von Database, die nichts
verbinden. Die Tests gegen den Test-Server (TEST_DATABASE_URL) prüfen die echte Database: eine Verbindung
je Aufruf, nur lesend, Zeitlimit.

Wichtig: Keine Meldung, kein Log und keine Ausgabe nennt Host, Benutzer, Passwort, Datenbankname oder
URL. Die Fehlerfälle nutzen deshalb Zugangswerte mit "geheim" (mcp_testkit.DB_SECRETS) und prüfen, dass
sie nirgends auftauchen.
"""

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import BASE_URL, DB_SECRETS, MCP_PATH, RPC_HEADERS, error_text, parse_rpc_body
from psycopg import errors
from starlette.testclient import TestClient

DB_LOGGER = "hoffmann_data.db"
# Port 1 ist auf Loopback nicht belegt, es antwortet nichts. Linux lehnt die Verbindung sofort ab
# (OperationalError). Windows lehnt nicht sofort ab, sondern wartet bis zum Zeitlimit connect_timeout=5;
# psycopg wirft dann ConnectionTimeout (der Test dauert dort etwa 5,5 Sekunden).
UNREACHABLE_URL = "postgresql://geheimuser:geheimpasswort@127.0.0.1:1/geheimdb"
SEARCH_ARGS = {"query": "Gurtband"}

ToolCall = Callable[..., dict[str, Any]]


class FakeDatabase:
    """Minimale Database: `connection()` gibt ein Objekt heraus oder wirft beim Betreten die übergebene Ausnahme."""

    def __init__(self, error_on_enter: BaseException | None = None) -> None:
        self.connection_object = object()
        self._error = error_on_enter

    @contextmanager
    def connection(self) -> Iterator[object]:
        if self._error is not None:
            raise self._error
        yield self.connection_object


def _db_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == DB_LOGGER]


def _all_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)


def _assert_nothing_leaked(
    secrets: tuple[str, ...],
    result: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keiner der Zugangswerte steht in Antwort, Log (alle Logger), stdout oder stderr."""
    captured = capsys.readouterr()
    haystacks = {
        "Antwort": json.dumps(result, ensure_ascii=False),
        "Log": _all_log_text(caplog),
        "stdout": captured.out,
        "stderr": captured.err,
    }
    for secret in secrets:
        for where, text in haystacks.items():
            assert secret not in text, f"{secret!r} steht in {where}"


# Fehlerfälle: (Fabrik der Ausnahme, Klassenname, SQLSTATE oder None). Der Text jeder Ausnahme enthält
# "geheim": Er darf nirgends erscheinen.
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


# read_connection ohne Datenbank


def test_constants_are_fixed_texts_without_values() -> None:
    from hoffmann_data import db

    assert db.NOT_CONFIGURED == "Die Datenbank ist nicht konfiguriert."
    assert db.DATABASE_ERROR.strip()
    assert db.DATABASE_ERROR != db.NOT_CONFIGURED
    assert "geheim" not in db.DATABASE_ERROR.lower()


def test_without_database_read_connection_raises_not_configured() -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.read_connection(None):
            pytest.fail("der Block darf ohne Datenbank nicht laufen")
    assert str(info.value) == db.NOT_CONFIGURED


def test_read_connection_gives_the_connection_of_the_database() -> None:
    from hoffmann_data import db

    fake = FakeDatabase()
    with db.read_connection(fake) as connection:
        assert connection is fake.connection_object


def test_tool_error_from_the_block_passes_through_unchanged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Eine ungültige Eingabe (ToolError) ist kein Datenbankfehler: nicht verschluckt, nicht umgeschrieben."""
    from hoffmann_data import db

    original = ToolError("feste Meldung zur Eingabe")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError) as info:
        with db.read_connection(FakeDatabase()):
            raise original
    assert info.value is original
    assert str(info.value) == "feste Meldung zur Eingabe"
    assert _db_records(caplog) == [], (
        "ein ToolError ist kein Datenbankfehler und wird nicht geloggt"
    )


@pytest.mark.parametrize(("make_error", "class_name", "sqlstate"), ERROR_CASES)
def test_errors_in_the_block_become_the_fixed_database_error(
    make_error: Callable[[], BaseException], class_name: str, sqlstate: str | None
) -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.read_connection(FakeDatabase()):
            raise make_error()
    assert str(info.value) == db.DATABASE_ERROR
    assert info.value.__cause__ is None
    # Auch der Kontext darf den Text der Ausnahme nicht mitführen (Traceback)
    assert info.value.__context__ is None or info.value.__suppress_context__


@pytest.mark.parametrize(("make_error", "class_name", "sqlstate"), ERROR_CASES)
def test_errors_from_opening_the_connection_become_the_fixed_database_error(
    make_error: Callable[[], BaseException], class_name: str, sqlstate: str | None
) -> None:
    from hoffmann_data import db

    with pytest.raises(ToolError) as info:
        with db.read_connection(FakeDatabase(error_on_enter=make_error())):
            pytest.fail("der Block darf nicht laufen, wenn die Verbindung fehlschlägt")
    assert str(info.value) == db.DATABASE_ERROR
    assert info.value.__cause__ is None
    assert info.value.__context__ is None or info.value.__suppress_context__


@pytest.mark.parametrize(("make_error", "class_name", "sqlstate"), ERROR_CASES)
def test_database_error_logs_exactly_one_warning_with_class_and_sqlstate_only(
    caplog: pytest.LogCaptureFixture,
    make_error: Callable[[], BaseException],
    class_name: str,
    sqlstate: str | None,
) -> None:
    from hoffmann_data import db

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError):
        with db.read_connection(FakeDatabase()):
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
        with db.read_connection(FakeDatabase(error_on_enter=psycopg.OperationalError("geheim"))):
            pytest.fail("der Block darf nicht laufen")
    records = _db_records(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert "geheim" not in records[0].getMessage()


# Fehlerfälle über die ganze App (ohne Test-Datenbank)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_products", {"query": "Gurtband"}),
        ("get_product", {"article_number": "FB-1001"}),
        ("find_customer", {"query": "firma.example"}),
    ],
)
def test_read_tools_without_database_report_not_configured(
    rpc: Callable[..., dict[str, Any]], tool: str, arguments: dict[str, Any]
) -> None:
    from hoffmann_data.db import NOT_CONFIGURED

    result = rpc("tools/call", {"name": tool, "arguments": arguments})["result"]
    assert NOT_CONFIGURED in error_text(result)
    assert not result.get("structuredContent")


def test_unreachable_database_gives_fixed_error_and_leaks_no_credentials(
    tool_call_for: Callable[[Any], ToolCall],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hoffmann_data.db import DATABASE_ERROR, Database

    caplog.set_level(logging.DEBUG)
    result = tool_call_for(Database(UNREACHABLE_URL))("search_products", SEARCH_ARGS)
    assert DATABASE_ERROR in error_text(result)
    assert not result.get("structuredContent")
    records = _db_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    # Linux: OperationalError, Windows: ConnectionTimeout (siehe UNREACHABLE_URL)
    assert re.search(r"[A-Za-z]+(Error|Timeout)", records[0].getMessage())
    _assert_nothing_leaked(DB_SECRETS, result, caplog, capsys)


def test_create_app_builds_the_database_from_the_configured_url(
    make_app: Callable[..., Any],
    token: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ohne Argument baut create_app die Database aus MCP_SERVER_DATABASE_URL: Der Fehler ist ein
    Datenbankfehler (Port unerreichbar), nicht "nicht konfiguriert"."""
    from hoffmann_data.db import DATABASE_ERROR, NOT_CONFIGURED

    caplog.set_level(logging.DEBUG)
    app = make_app({"MCP_SERVER_TOKEN": token, "MCP_SERVER_DATABASE_URL": UNREACHABLE_URL})
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search_products", "arguments": SEARCH_ARGS},
    }
    headers = {**RPC_HEADERS, "Authorization": f"Bearer {token}"}
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    result = parse_rpc_body(response)["result"]
    text = error_text(result)
    assert DATABASE_ERROR in text
    assert NOT_CONFIGURED not in text
    _assert_nothing_leaked(DB_SECRETS, result, caplog, capsys)


def test_database_constructor_does_not_connect(connect_log: Any) -> None:
    from hoffmann_data.db import Database

    Database(UNREACHABLE_URL)
    assert connect_log.calls == 0


def test_database_repr_does_not_show_the_url() -> None:
    from hoffmann_data.db import Database

    database = Database(UNREACHABLE_URL)
    for text in (repr(database), str(database)):
        for secret in DB_SECRETS:
            assert secret not in text
        assert "127.0.0.1" not in text


# Die echte Database gegen den Test-Server


def test_nonexistent_database_on_the_test_server_leaks_no_credentials(
    db_url: str,
    tool_call_for: Callable[[Any], ToolCall],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hoffmann_data.db import DATABASE_ERROR, Database

    parts = urlsplit(db_url)
    missing_name = f"gibt_es_nicht_{uuid.uuid4().hex[:12]}"
    missing_url = parts._replace(path=f"/{missing_name}").geturl()
    secrets = tuple(s for s in (parts.username, parts.password) if s and len(s) >= 4)
    caplog.set_level(logging.DEBUG)
    result = tool_call_for(Database(missing_url))("search_products", SEARCH_ARGS)
    assert DATABASE_ERROR in error_text(result)
    assert len(_db_records(caplog)) == 1
    _assert_nothing_leaked((*secrets, missing_name), result, caplog, capsys)


def test_every_call_opens_and_closes_its_own_connection(
    real_database: Any, connect_log: Any
) -> None:
    before = connect_log.calls
    with real_database.connection() as first:
        first.execute("SELECT 1")
    with real_database.connection() as second:
        second.execute("SELECT 1")
    assert connect_log.calls - before == 2
    first_and_second = connect_log.opened[-2:]
    assert first_and_second[0] is not first_and_second[1]
    assert all(connection.closed for connection in first_and_second)


def test_connection_is_closed_after_an_error_in_the_block(
    real_database: Any, connect_log: Any
) -> None:
    with pytest.raises(RuntimeError):
        with real_database.connection():
            raise RuntimeError("Abbruch im Block")
    assert connect_log.opened[-1].closed


def test_each_tool_request_uses_a_fresh_connection(
    real_database: Any, tool_call_for: Callable[[Any], ToolCall], connect_log: Any
) -> None:
    call = tool_call_for(real_database)
    before = connect_log.calls
    first = call("search_products", SEARCH_ARGS)
    second = call("search_products", SEARCH_ARGS)
    assert first.get("isError") is not True and second.get("isError") is not True
    assert connect_log.calls - before == 2, "kein Pool: je Anfrage eine Verbindung"
    assert all(connection.closed for connection in connect_log.opened[-2:])


def test_connection_is_read_only(real_database: Any) -> None:
    with real_database.connection() as connection:
        with pytest.raises(errors.ReadOnlySqlTransaction):
            connection.execute("INSERT INTO products DEFAULT VALUES")


def test_connection_has_a_five_second_statement_timeout(real_database: Any) -> None:
    with real_database.connection() as connection:
        row = connection.execute("SHOW statement_timeout").fetchone()
    assert row == ("5s",)


def test_connection_does_not_use_prepared_statements(real_database: Any) -> None:
    """prepare_threshold=None: kein serverseitiges Vorbereiten (Pooler im Transaktionsmodus, z. B. Supabase)."""
    with real_database.connection() as connection:
        assert connection.prepare_threshold is None


def test_connection_has_a_connect_timeout(real_database: Any) -> None:
    with real_database.connection() as connection:
        assert connection.info.get_parameters().get("connect_timeout") == "5"
