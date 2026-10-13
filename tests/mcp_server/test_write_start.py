"""Tests des Starts mit Schreibweg: main() und die Startprüfung der Schreibrolle (F08, ADR 0005 Punkte 2 und 7).

Schnittstelle, die diese Tests festlegen (__main__.py):
- Ohne Schreib-Variablen läuft die Schreibprüfung nicht; der Start bleibt wie in F07.
- Mit beiden Schreib-Variablen ruft main() nach db.verify_read_only_role(db.Database(<Lese-URL>)) und vor
  uvicorn.run genau einmal db.verify_write_role(db.Database(<Schreib-URL>)), beide über das Modul db.
  Die Schreibprüfung bekommt eine schreibgeschützte db.Database, nie eine db.WriteDatabase: Die Prüfung liest nur,
  und die Verbindung erzwingt das zusätzlich.
- Scheitert die Lese-Prüfung, bricht der Start dort ab (die Schreibprüfung läuft nicht). Scheitert die
  Schreibprüfung: Exit-Code 1, kein uvicorn.run, auf stderr steht "Schreibweg: " und danach die Kurzbezeichnungen.
  Nie URL, Rollenname, Token oder Zugangswerte, auf stdout und stderr nicht.
- Konfigurationsfehler des Schreibpaars (nur eine Variable, gleiche Token, ungültige URL) stoppen den Start vor
  jeder Prüfung. Es gibt keinen Schalter, der die Schreibprüfung abschaltet.

Ohne Datenbank; der letzte Test verbindet gegen einen unerreichbaren Port. Rot, bis __main__.py den Schreibweg kennt.
"""

import logging
import re
from typing import Any

import pytest
from mcp_testkit import (
    DB_SECRETS,
    DBURL_INVALID,
    GOOD_READ_URL,
    GOOD_WRITE_URL,
    WRITE_SECRETS,
    WRITE_TOKEN_NAME,
    WRITE_URL_NAME,
)

READ_URL_NAME = "MCP_SERVER_DATABASE_URL"
DB_LOGGER = "hoffmann_data.db"
PREFIX = "Schreibweg:"
# Port 1 auf Loopback: Linux lehnt die Verbindung sofort ab, Windows wartet bis connect_timeout=5 (ca. 5,5 Sekunden)
UNREACHABLE_WRITE_URL = "postgresql://geheimschreiber:geheimschreibpasswort@127.0.0.1:1/geheimdb"
SHORT_NAMES = ("rolle-ist-superuser", "schreibrecht:UPDATE:customers")


class Control:
    """Ereignisse und Aufrufe von main(): Prüfungen und uvicorn.run sind ersetzt."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.read_calls: list[Any] = []
        self.write_calls: list[Any] = []
        self.run_calls: list[dict[str, Any]] = []


def _control_main(
    monkeypatch: pytest.MonkeyPatch,
    read_failure: Exception | None = None,
    write_failure: Exception | None = None,
) -> Control:
    import uvicorn
    from hoffmann_data import db

    control = Control()
    original_database = db.Database

    class SpyDatabase(original_database):  # type: ignore[valid-type, misc]
        """Eine echte Database, die sich ihre URL merkt (verbindet nie von selbst)."""

        def __init__(self, url: str, **kwargs: Any) -> None:
            super().__init__(url, **kwargs)
            self.url = url

    def fake_read(database: Any) -> None:
        control.events.append("verify")
        control.read_calls.append(database)
        if read_failure is not None:
            raise read_failure

    def fake_write(database: Any) -> None:
        control.events.append("verify_write")
        control.write_calls.append(database)
        if write_failure is not None:
            raise write_failure

    def fake_run(app: Any, **kwargs: Any) -> None:
        control.events.append("run")
        control.run_calls.append(kwargs)

    monkeypatch.setattr(db, "Database", SpyDatabase)
    monkeypatch.setattr(db, "verify_read_only_role", fake_read)
    monkeypatch.setattr(db, "verify_write_role", fake_write)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    return control


def _assert_no_values(text: str, *values: str) -> None:
    for value in (*values, GOOD_READ_URL, GOOD_WRITE_URL, *DB_SECRETS, *WRITE_SECRETS):
        # Ein leerer Wert (z. B. fehlendes Token) ist in jedem Text enthalten und sagt nichts
        if value:
            assert value not in text, value
    assert "postgresql://" not in text


def test_the_write_check_does_not_run_without_write_config(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch)
    main({"MCP_SERVER_TOKEN": token, READ_URL_NAME: GOOD_READ_URL})
    assert control.events == ["verify", "run"]
    assert control.write_calls == []


def test_the_write_check_runs_after_the_read_check_and_before_serving(
    write_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch)
    main(write_env)
    assert control.events == ["verify", "verify_write", "run"]
    assert len(control.read_calls) == 1 and len(control.write_calls) == 1
    assert len(control.run_calls) == 1


def test_each_check_gets_its_own_url_and_a_read_only_database(
    write_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch)
    main(write_env)
    (read_database,) = control.read_calls
    (write_database,) = control.write_calls
    assert read_database.url == GOOD_READ_URL
    assert write_database.url == GOOD_WRITE_URL
    assert isinstance(write_database, db.Database)
    assert not isinstance(write_database, db.WriteDatabase)


def test_a_failed_write_check_stops_the_start(
    write_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch, write_failure=db.RoleCheckError(SHORT_NAMES))
    with pytest.raises(SystemExit) as info:
        main(write_env)
    assert info.value.code == 1
    assert control.events == ["verify", "verify_write"]
    assert control.run_calls == []
    captured = capsys.readouterr()
    assert PREFIX in captured.err
    for short in SHORT_NAMES:
        assert short in captured.err
    _assert_no_values(
        captured.out + captured.err, write_env["MCP_SERVER_TOKEN"], write_env[WRITE_TOKEN_NAME]
    )


def test_a_failed_read_check_stops_before_the_write_check(
    write_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch, read_failure=db.RoleCheckError(SHORT_NAMES))
    with pytest.raises(SystemExit) as info:
        main(write_env)
    assert info.value.code == 1
    assert control.events == ["verify"]
    assert control.write_calls == [] and control.run_calls == []
    captured = capsys.readouterr()
    assert SHORT_NAMES[0] in captured.err
    assert PREFIX not in captured.err


@pytest.mark.parametrize("variable", [WRITE_URL_NAME, WRITE_TOKEN_NAME])
def test_one_write_variable_alone_stops_the_start_before_any_check(
    write_env: dict[str, str],
    variable: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    env = {k: v for k, v in write_env.items() if k not in (WRITE_URL_NAME, WRITE_TOKEN_NAME)}
    env[variable] = write_env[variable]
    control = _control_main(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main(env)
    assert info.value.code == 1
    assert control.events == []
    captured = capsys.readouterr()
    assert WRITE_URL_NAME in captured.err and WRITE_TOKEN_NAME in captured.err
    _assert_no_values(
        captured.out + captured.err, env["MCP_SERVER_TOKEN"], env.get(WRITE_TOKEN_NAME, "")
    )


def test_equal_tokens_stop_the_start_before_any_check(
    write_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    env = {**write_env, WRITE_TOKEN_NAME: write_env["MCP_SERVER_TOKEN"]}
    control = _control_main(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main(env)
    assert info.value.code == 1
    assert control.events == []
    captured = capsys.readouterr()
    assert WRITE_TOKEN_NAME in captured.err
    _assert_no_values(captured.out + captured.err, env["MCP_SERVER_TOKEN"])


@pytest.mark.parametrize("case", ["remote-ohne-sslmode", "zwei-at", "param-host"])
def test_an_invalid_write_url_stops_the_start(
    write_env: dict[str, str],
    case: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    url = next(u for c, u, _ in DBURL_INVALID if c == case)
    assert url is not None
    control = _control_main(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main({**write_env, WRITE_URL_NAME: url})
    assert info.value.code == 1
    assert control.events == []
    captured = capsys.readouterr()
    assert WRITE_URL_NAME in captured.err
    _assert_no_values(captured.out + captured.err, url, write_env[WRITE_TOKEN_NAME])


def test_start_with_write_config_serves_once_and_prints_no_secret(
    write_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch)
    main(write_env)
    assert len(control.run_calls) == 1
    assert control.run_calls[0]["host"] == "127.0.0.1"
    assert control.run_calls[0]["access_log"] is False
    captured = capsys.readouterr()
    _assert_no_values(
        captured.out + captured.err, write_env["MCP_SERVER_TOKEN"], write_env[WRITE_TOKEN_NAME]
    )


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SKIP_ROLE_CHECK", "1"),
        ("DISABLE_ROLE_CHECK", "true"),
        ("MCP_SERVER_SKIP_WRITE_CHECK", "1"),
        ("MCP_SERVER_DISABLE_WRITE_CHECK", "true"),
        ("MCP_SERVER_UNSAFE", "1"),
        ("MCP_SERVER_WRITE_NO_CHECK", "true"),
    ],
)
def test_switch_variables_change_nothing_for_the_write_check(
    write_env: dict[str, str], variable: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    control = _control_main(monkeypatch, write_failure=db.RoleCheckError(SHORT_NAMES))
    with pytest.raises(SystemExit) as info:
        main({**write_env, variable: value})
    assert info.value.code == 1
    assert len(control.write_calls) == 1 and control.run_calls == []


def test_an_unreachable_write_database_stops_the_start_with_a_fixed_message(
    write_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die echte Schreibprüfung, nur die Lese-Prüfung und uvicorn.run sind ersetzt (Linux: OperationalError,
    Windows: ConnectionTimeout nach etwa 5 Sekunden; der Test nennt keine einzelne Klasse)."""
    import uvicorn
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    run_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(db, "verify_read_only_role", lambda database: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: run_calls.append(kwargs))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SystemExit) as info:
        main({**write_env, WRITE_URL_NAME: UNREACHABLE_WRITE_URL})
    assert info.value.code == 1
    assert run_calls == []
    captured = capsys.readouterr()
    assert PREFIX in captured.err and "datenbankfehler" in captured.err
    log_text = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    for where, text in {"stdout": captured.out, "stderr": captured.err, "Log": log_text}.items():
        assert UNREACHABLE_WRITE_URL not in text, where
        assert write_env["MCP_SERVER_TOKEN"] not in text, where
        assert write_env[WRITE_TOKEN_NAME] not in text, where
        for secret in (*DB_SECRETS, *WRITE_SECRETS):
            assert secret not in text, f"{secret!r} steht in {where}"
    records = [r for r in caplog.records if r.name == DB_LOGGER]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert re.search(r"[A-Za-z]+(Error|Timeout)", records[0].getMessage())
