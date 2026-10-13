"""Tests der Konfiguration des Schreibwegs: MCP_SERVER_WRITE_DATABASE_URL und MCP_SERVER_WRITE_TOKEN (F08, ADR 0005 Punkt 2).

Schnittstelle, die diese Tests festlegen (config.py):
- config.WRITE_DATABASE_URL_VAR und config.WRITE_TOKEN_VAR tragen die Namen der beiden Variablen.
- Config.write_database_url und Config.write_token (beide None ohne Schreibweg, beide nicht in repr) und die
  Eigenschaft Config.write_enabled (beide gesetzt).
- Leer oder nur Leerraum gilt als nicht gesetzt (wie die übrigen optionalen Variablen).
- Beide fehlen: kein Schreibweg, kein Fehler. Nur eine gesetzt: ConfigError, die Meldung nennt beide Namen.
  Beide Token gleich: ConfigError, die Meldung nennt beide Namen.
- Gleiche Regeln wie die Lese-Variablen: Das Schreib-Token wie MCP_SERVER_TOKEN, die Schreib-URL wie
  MCP_SERVER_DATABASE_URL (dburl.py). Auch ohne require_database wird eine gesetzte Schreib-URL geprüft.
- Reihenfolge der Prüfungen: erst alles bisherige (Lese-Token, Host, Port, erlaubte Hosts, Lese-URL), dann das
  Schreibpaar: Vollständigkeit, Schreib-Token, Schreib-URL, gleiche Token.
- Meldungen nennen nur Variablennamen, nie einen Wert (Token, URL oder Teile davon).

Ohne Datenbank und ohne Netzwerk. Diese Tests sind rot, bis config.py den Schreibweg kennt (TDD).
"""

from typing import Any

import pytest
from mcp_testkit import (
    DB_SECRETS,
    DBURL_INVALID,
    DBURL_VALID,
    GOOD_READ_URL,
    GOOD_WRITE_URL,
    WRITE_SECRETS,
    WRITE_TOKEN_NAME,
    WRITE_URL_NAME,
)

READ_TOKEN_NAME = "MCP_SERVER_TOKEN"
READ_URL_NAME = "MCP_SERVER_DATABASE_URL"
# 40 verschiedene Zeichen: Die Fälle mit Leerzeichen/Umbruch sollen an diesen scheitern, nicht an der Mindestzahl
LONG = "abcdefghijklmnopqrstuvwxyz0123456789ABCD"
BLANKS = ["", " ", "   ", "\t", " \t "]


def _load(env: dict[str, str], **kwargs: Any) -> Any:
    from hoffmann_data.config import load_config

    return load_config(env, **kwargs)


def _error_type() -> type[Exception]:
    from hoffmann_data.config import ConfigError

    return ConfigError


def _message(env: dict[str, str], **kwargs: Any) -> str:
    """Text des ConfigError; bricht ab, wenn die Konfiguration gültig ist."""
    with pytest.raises(_error_type()) as info:
        _load(env, **kwargs)
    return str(info.value)


def _with_write(token: str, write_token: str, url: str = GOOD_WRITE_URL) -> dict[str, str]:
    return {READ_TOKEN_NAME: token, WRITE_URL_NAME: url, WRITE_TOKEN_NAME: write_token}


def test_variable_names_are_exported() -> None:
    from hoffmann_data import config

    assert config.WRITE_DATABASE_URL_VAR == WRITE_URL_NAME == "MCP_SERVER_WRITE_DATABASE_URL"
    assert config.WRITE_TOKEN_VAR == WRITE_TOKEN_NAME == "MCP_SERVER_WRITE_TOKEN"


# Kein Schreibweg


def test_without_both_variables_there_is_no_write_path(token: str) -> None:
    """Die bestehenden Aufrufe load_config({"MCP_SERVER_TOKEN": ...}) bleiben gültig."""
    config = _load({READ_TOKEN_NAME: token})
    assert config.write_database_url is None
    assert config.write_token is None
    assert config.write_enabled is False


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("which", ["url", "token", "both"])
def test_blank_values_count_as_not_set(token: str, blank: str, which: str) -> None:
    env = {READ_TOKEN_NAME: token}
    if which in ("url", "both"):
        env[WRITE_URL_NAME] = blank
    if which in ("token", "both"):
        env[WRITE_TOKEN_NAME] = blank
    config = _load(env)
    assert config.write_database_url is None and config.write_token is None
    assert config.write_enabled is False


# Beide gesetzt


@pytest.mark.parametrize(("case", "url"), DBURL_VALID, ids=[c for c, _ in DBURL_VALID])
def test_both_valid_variables_are_kept_unchanged(
    token: str, write_token: str, case: str, url: str
) -> None:
    config = _load(_with_write(token, write_token, url))
    assert config.write_database_url == url
    assert config.write_token == write_token
    assert config.write_enabled is True


def test_the_write_path_loads_together_with_the_read_database(token: str, write_token: str) -> None:
    env = {**_with_write(token, write_token), READ_URL_NAME: GOOD_READ_URL}
    config = _load(env, require_database=True)
    assert config.database_url == GOOD_READ_URL
    assert config.write_database_url == GOOD_WRITE_URL
    assert config.token == token and config.write_token == write_token


@pytest.mark.parametrize(
    "good",
    [
        pytest.param("abcdefghijklmnopqrstuvwxyz012345", id="genau-32-zeichen"),
        pytest.param("abcdefghij" * 4, id="genau-10-verschiedene"),
    ],
)
def test_a_valid_write_token_is_accepted_at_the_boundaries(token: str, good: str) -> None:
    assert good != token
    assert _load(_with_write(token, good)).write_token == good


# Nur eine gesetzt


@pytest.mark.parametrize("other", [None, "", "   "], ids=["fehlt", "leer", "leerraum"])
def test_only_the_url_is_an_error(token: str, other: str | None) -> None:
    env = {READ_TOKEN_NAME: token, WRITE_URL_NAME: GOOD_WRITE_URL}
    if other is not None:
        env[WRITE_TOKEN_NAME] = other
    message = _message(env)
    assert WRITE_URL_NAME in message and WRITE_TOKEN_NAME in message
    for secret in (*WRITE_SECRETS, *DB_SECRETS):
        assert secret not in message, secret


@pytest.mark.parametrize("other", [None, "", "   "], ids=["fehlt", "leer", "leerraum"])
def test_only_the_token_is_an_error(token: str, write_token: str, other: str | None) -> None:
    env = {READ_TOKEN_NAME: token, WRITE_TOKEN_NAME: write_token}
    if other is not None:
        env[WRITE_URL_NAME] = other
    message = _message(env)
    assert WRITE_URL_NAME in message and WRITE_TOKEN_NAME in message
    assert write_token not in message


def test_a_blank_value_does_not_complete_the_pair(token: str, write_token: str) -> None:
    """Gültige Schreib-URL mit leerem Token (und umgekehrt) ist ein Paarfehler, kein stilles Abschalten."""
    assert WRITE_TOKEN_NAME in _message(
        {READ_TOKEN_NAME: token, WRITE_URL_NAME: GOOD_WRITE_URL, WRITE_TOKEN_NAME: "  "}
    )
    assert WRITE_URL_NAME in _message(
        {READ_TOKEN_NAME: token, WRITE_URL_NAME: "", WRITE_TOKEN_NAME: write_token}
    )


def test_equal_tokens_are_an_error(token: str) -> None:
    message = _message(_with_write(token, token))
    assert WRITE_TOKEN_NAME in message and READ_TOKEN_NAME in message
    assert token not in message


# Gleiche Regeln wie die Lese-Variablen


@pytest.mark.parametrize(
    "weak",
    [
        pytest.param("a" * 32, id="ein-zeichen"),
        pytest.param("ab" * 16, id="zwei-zeichen"),
        pytest.param("abcdefghi" * 4, id="neun-verschiedene-zeichen"),
        pytest.param("a" * 200, id="lang-aber-eintoenig"),
        pytest.param("a" * 31, id="31-zeichen"),
        pytest.param("kurz", id="kurz"),
        pytest.param(LONG[:20] + " " + LONG[:20], id="leerzeichen-in-der-mitte"),
        pytest.param(" " + LONG, id="leerzeichen-vorn"),
        pytest.param(LONG + " ", id="leerzeichen-hinten"),
        pytest.param(LONG + "\n", id="zeilenumbruch-hinten"),
        pytest.param("\n" + LONG, id="zeilenumbruch-vorn"),
        pytest.param(LONG + "\r\n", id="crlf-hinten"),
        pytest.param(LONG[:20] + "\t" + LONG[:20], id="tabulator"),
        pytest.param("ä" * 40, id="nicht-ascii"),
    ],
)
def test_write_token_follows_the_read_token_rules(token: str, weak: str) -> None:
    """Abgelehnt, nicht gekürzt. Die Meldung nennt den Schreib-Namen, nie den Wert und nie den Lese-Namen."""
    message = _message(_with_write(token, weak))
    assert WRITE_TOKEN_NAME in message
    assert READ_TOKEN_NAME not in message
    assert weak not in message and weak.strip() not in message and repr(weak) not in message


@pytest.mark.parametrize(
    ("case", "url", "environ"),
    [item for item in DBURL_INVALID if item[1] and item[1].strip()],
    ids=[c for c, u, _ in DBURL_INVALID if u and u.strip()],
)
def test_write_url_follows_the_read_url_rules(
    token: str, write_token: str, case: str, url: str, environ: dict[str, str]
) -> None:
    """Auch ohne require_database: Eine gesetzte, aber ungültige Schreib-URL ist ein Fehler."""
    message = _message({**_with_write(token, write_token, url), **environ})
    assert WRITE_URL_NAME in message
    assert READ_URL_NAME not in message
    for secret in (*DB_SECRETS, *WRITE_SECRETS):
        assert secret not in message, secret
    assert url.strip() not in message


# Reihenfolge der Prüfungen


@pytest.mark.parametrize(
    ("case", "extra", "kwargs", "expected_name"),
    [
        pytest.param("token", {READ_TOKEN_NAME: "kurz"}, {}, READ_TOKEN_NAME, id="lese-token"),
        pytest.param(
            "port", {"MCP_SERVER_PORT": "abc"}, {}, "MCP_SERVER_PORT", id="port-wie-bisher"
        ),
        pytest.param(
            "hosts",
            {"MCP_SERVER_HOST": "0.0.0.0"},
            {},
            "MCP_SERVER_ALLOWED_HOSTS",
            id="fremder-host-ohne-liste",
        ),
        pytest.param(
            "url",
            {READ_URL_NAME: "postgresql://geheimuser:geheim@passwort@localhost/geheimdb"},
            {},
            READ_URL_NAME,
            id="lese-url-ungueltig",
        ),
        pytest.param("fehlt", {}, {"require_database": True}, READ_URL_NAME, id="lese-url-fehlt"),
    ],
)
def test_the_existing_checks_come_first(
    token: str,
    write_token: str,
    case: str,
    extra: dict[str, str],
    kwargs: dict[str, Any],
    expected_name: str,
) -> None:
    """Mit einem unvollständigen Schreibpaar nennt die Meldung den früheren Fehler, nicht das Paar."""
    env = {READ_TOKEN_NAME: token, WRITE_TOKEN_NAME: write_token, **extra}
    message = _message(env, **kwargs)
    assert expected_name in message
    assert WRITE_URL_NAME not in message


def test_the_pair_is_checked_before_the_token_rules(token: str) -> None:
    """Nur ein (zu kurzes) Token: Der Fehler ist das fehlende Paar, nicht die Token-Regel."""
    message = _message({READ_TOKEN_NAME: token, WRITE_TOKEN_NAME: "kurz"})
    assert WRITE_URL_NAME in message and WRITE_TOKEN_NAME in message


def test_the_write_token_rules_come_before_the_url_rules(token: str) -> None:
    message = _message(_with_write(token, "kurz", "mysql://geheimuser:geheimpasswort@localhost/x"))
    assert WRITE_TOKEN_NAME in message
    assert WRITE_URL_NAME not in message


def test_the_url_rules_come_before_the_equality_check(token: str) -> None:
    message = _message(_with_write(token, token, "mysql://geheimuser:geheimpasswort@localhost/x"))
    assert WRITE_URL_NAME in message
    assert WRITE_TOKEN_NAME not in message


# Keine Werte


def test_config_repr_and_str_hide_the_write_values(token: str, write_token: str) -> None:
    config = _load(_with_write(token, write_token))
    assert config.write_database_url == GOOD_WRITE_URL
    for text in (repr(config), str(config)):
        assert GOOD_WRITE_URL not in text
        assert write_token not in text
        for secret in (*WRITE_SECRETS, *DB_SECRETS):
            assert secret not in text, secret


def test_no_error_contains_a_value(token: str, write_token: str) -> None:
    """Sammeltest: Kein Fehlerfall des Schreibpaars nennt ein Token oder einen Teil einer URL."""
    bad_token = "k" * 10 + " " + "k" * 30
    bad_url = "postgresql://geheimschreiber:geheim@schreibpasswort@localhost/geheimdb"
    cases = [
        {READ_TOKEN_NAME: token, WRITE_URL_NAME: GOOD_WRITE_URL},
        {READ_TOKEN_NAME: token, WRITE_TOKEN_NAME: write_token},
        _with_write(token, token),
        _with_write(token, bad_token),
        _with_write(token, write_token, bad_url),
    ]
    values = (token, write_token, bad_token, bad_url, GOOD_WRITE_URL, *DB_SECRETS, *WRITE_SECRETS)
    for env in cases:
        message = _message(env)
        for value in values:
            assert value not in message, value
