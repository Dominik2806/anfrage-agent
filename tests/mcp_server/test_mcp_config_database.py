"""Tests der Konfiguration MCP_SERVER_DATABASE_URL und des Starts (F07).

Die Variable kommt nur aus der Umgebung. Ohne sie startet die App in den Tests weiter (create_app ohne
Datenbank), der Start über main() verlangt sie aber. Fehlermeldungen nennen nur den Namen der Variablen,
nie einen Teil der URL. Die Regeln der URL selbst prüft test_dburl.py.
"""

from typing import Any

import pytest
from mcp_testkit import DB_SECRETS, DBURL_INVALID, DBURL_VALID

NAME = "MCP_SERVER_DATABASE_URL"
GOOD_URL = "postgresql://geheimuser:geheimpasswort@localhost:5432/geheimdb"


def _load(env: dict[str, str], **kwargs: Any) -> Any:
    from hoffmann_data.config import load_config

    return load_config(env, **kwargs)


def _error_type() -> type[Exception]:
    from hoffmann_data.config import ConfigError

    return ConfigError


def test_variable_name_is_exported() -> None:
    from hoffmann_data import config

    assert config.DATABASE_URL_VAR == NAME


def test_without_the_variable_the_config_loads_and_url_is_none(token: str) -> None:
    """Die bestehenden F06-Aufrufe load_config({"MCP_SERVER_TOKEN": ...}) bleiben gültig."""
    assert _load({"MCP_SERVER_TOKEN": token}).database_url is None


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", " \t "])
def test_blank_value_counts_as_not_set(token: str, blank: str) -> None:
    assert _load({"MCP_SERVER_TOKEN": token, NAME: blank}).database_url is None


@pytest.mark.parametrize(("case", "url"), DBURL_VALID, ids=[c for c, _ in DBURL_VALID])
def test_valid_url_is_kept_unchanged(token: str, case: str, url: str) -> None:
    assert _load({"MCP_SERVER_TOKEN": token, NAME: url}).database_url == url


@pytest.mark.parametrize(
    ("case", "url", "environ"),
    [item for item in DBURL_INVALID if item[1] and item[1].strip()],
    ids=[c for c, u, _ in DBURL_INVALID if u and u.strip()],
)
def test_invalid_url_is_rejected_even_when_not_required(
    token: str, case: str, url: str, environ: dict[str, str]
) -> None:
    """Auch ohne require_database: Eine gesetzte, aber ungültige URL ist ein Fehler, kein Ignorieren."""
    with pytest.raises(_error_type()) as info:
        _load({"MCP_SERVER_TOKEN": token, NAME: url, **environ})
    message = str(info.value)
    assert NAME in message
    for secret in DB_SECRETS:
        assert secret not in message, secret
    assert url.strip() not in message


def test_pg_environment_overrides_in_the_given_env_are_rejected(token: str) -> None:
    """PGHOSTADDR und PGSERVICE stehen in der übergebenen Umgebung und überstimmen den Host der URL."""
    with pytest.raises(_error_type()) as info:
        _load({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL, "PGHOSTADDR": "10.0.0.1"})
    assert NAME in str(info.value)


@pytest.mark.parametrize("env_value", [None, "", "   "], ids=["fehlt", "leer", "nur-leerzeichen"])
def test_missing_url_is_an_error_when_required(token: str, env_value: str | None) -> None:
    env = {"MCP_SERVER_TOKEN": token}
    if env_value is not None:
        env[NAME] = env_value
    with pytest.raises(_error_type()) as info:
        _load(env, require_database=True)
    assert NAME in str(info.value)


def test_valid_url_passes_when_required(token: str) -> None:
    config = _load({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL}, require_database=True)
    assert config.database_url == GOOD_URL


def test_the_token_is_still_checked_first() -> None:
    """Mit ungültigem Token und fehlender URL nennt die Meldung das Token."""
    with pytest.raises(_error_type()) as info:
        _load({"MCP_SERVER_TOKEN": "kurz"}, require_database=True)
    assert "MCP_SERVER_TOKEN" in str(info.value)


def test_config_repr_and_str_do_not_reveal_the_database_url(token: str) -> None:
    config = _load({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL})
    for text in (repr(config), str(config)):
        assert GOOD_URL not in text
        for secret in DB_SECRETS:
            assert secret not in text, secret


# Start über main()


def _block_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import uvicorn

    calls: list[dict[str, Any]] = []

    def record(app: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(uvicorn, "run", record)
    return calls


def test_start_without_database_url_fails_before_serving(
    token: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    calls = _block_uvicorn(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main({"MCP_SERVER_TOKEN": token})
    assert info.value.code == 1
    assert NAME in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize(
    "case", ["remote-ohne-sslmode", "param-host", "zwei-at", "raute-im-passwort"]
)
def test_start_with_invalid_database_url_fails_without_leaking(
    token: str,
    case: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    url = next(u for c, u, _ in DBURL_INVALID if c == case)
    calls = _block_uvicorn(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main({"MCP_SERVER_TOKEN": token, NAME: url})
    assert info.value.code == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert NAME in captured.err
    assert token not in output
    for secret in DB_SECRETS:
        assert secret not in output, secret
    assert calls == []


def test_start_with_valid_database_url_serves_once(
    token: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    calls = _block_uvicorn(monkeypatch)
    main({"MCP_SERVER_TOKEN": token, NAME: GOOD_URL})
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["access_log"] is False
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert GOOD_URL not in output
    for secret in DB_SECRETS:
        assert secret not in output
