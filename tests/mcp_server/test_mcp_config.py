"""Tests der Konfiguration und des Starts (F06): Token-Regeln, Host, Port, erlaubte Hosts."""

from typing import Any

import pytest

LONG = "a" * 40


def _load(env: dict[str, str]) -> Any:
    from hoffmann_data.config import load_config

    return load_config(env)


def _error_type() -> type[Exception]:
    from hoffmann_data.config import ConfigError

    return ConfigError


def test_valid_token_is_accepted(token: str) -> None:
    config = _load({"MCP_SERVER_TOKEN": token})
    assert config.token == token


def test_defaults_are_loopback_and_port_8000(token: str) -> None:
    config = _load({"MCP_SERVER_TOKEN": token})
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert tuple(config.allowed_hosts) == ()


def test_token_of_exactly_32_chars_is_accepted() -> None:
    assert _load({"MCP_SERVER_TOKEN": "a" * 32}).token == "a" * 32


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({}, id="fehlt"),
        pytest.param({"MCP_SERVER_TOKEN": ""}, id="leer"),
        pytest.param({"MCP_SERVER_TOKEN": "   "}, id="nur-leerzeichen"),
        pytest.param({"MCP_SERVER_TOKEN": "a" * 31}, id="31-zeichen"),
        pytest.param({"MCP_SERVER_TOKEN": "kurz"}, id="kurz"),
    ],
)
def test_missing_empty_or_short_token_is_rejected(env: dict[str, str]) -> None:
    with pytest.raises(_error_type()) as info:
        _load(env)
    assert "MCP_SERVER_TOKEN" in str(info.value)
    assert "32" in str(info.value)


@pytest.mark.parametrize(
    "bad_token",
    [
        pytest.param(LONG[:20] + " " + LONG[:20], id="leerzeichen-in-der-mitte"),
        pytest.param(" " + LONG, id="leerzeichen-vorn"),
        pytest.param(LONG + " ", id="leerzeichen-hinten"),
        pytest.param(LONG + "\n", id="zeilenumbruch-hinten"),
        pytest.param("\n" + LONG, id="zeilenumbruch-vorn"),
        pytest.param(LONG + "\r\n", id="crlf-hinten"),
        pytest.param(LONG[:20] + "\n" + LONG[:20], id="zeilenumbruch-in-der-mitte"),
        pytest.param(LONG[:20] + "\t" + LONG[:20], id="tabulator"),
        pytest.param("ä" * 40, id="nicht-ascii"),
    ],
)
def test_token_with_whitespace_or_non_ascii_is_rejected_not_trimmed(bad_token: str) -> None:
    """Ein lang genügendes Token mit Leerzeichen/Umbruch wird abgelehnt, nicht gekürzt."""
    with pytest.raises(_error_type()) as info:
        _load({"MCP_SERVER_TOKEN": bad_token})
    assert "MCP_SERVER_TOKEN" in str(info.value)


@pytest.mark.parametrize("port", ["abc", "0", "-1", "65536", ""])
def test_invalid_port_is_rejected(token: str, port: str) -> None:
    with pytest.raises(_error_type()) as info:
        _load({"MCP_SERVER_TOKEN": token, "MCP_SERVER_PORT": port})
    assert "MCP_SERVER_PORT" in str(info.value)


def test_port_can_be_set(token: str) -> None:
    assert _load({"MCP_SERVER_TOKEN": token, "MCP_SERVER_PORT": "9001"}).port == 9001


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_start_without_allowed_hosts(token: str, host: str) -> None:
    assert _load({"MCP_SERVER_TOKEN": token, "MCP_SERVER_HOST": host}).host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "daten.example.org"])
def test_non_loopback_host_without_allowed_hosts_is_rejected(token: str, host: str) -> None:
    with pytest.raises(_error_type()) as info:
        _load({"MCP_SERVER_TOKEN": token, "MCP_SERVER_HOST": host})
    assert "MCP_SERVER_ALLOWED_HOSTS" in str(info.value)


def test_non_loopback_host_with_allowed_hosts_is_accepted(token: str) -> None:
    config = _load(
        {
            "MCP_SERVER_TOKEN": token,
            "MCP_SERVER_HOST": "0.0.0.0",
            "MCP_SERVER_ALLOWED_HOSTS": "daten.example.org, daten.example.org:*",
        }
    )
    assert config.host == "0.0.0.0"
    assert tuple(config.allowed_hosts) == ("daten.example.org", "daten.example.org:*")


@pytest.mark.parametrize("value", [",", "a.example,,b.example", "   "])
def test_empty_entries_in_allowed_hosts_are_rejected(token: str, value: str) -> None:
    with pytest.raises(_error_type()) as info:
        _load(
            {
                "MCP_SERVER_TOKEN": token,
                "MCP_SERVER_HOST": "0.0.0.0",
                "MCP_SERVER_ALLOWED_HOSTS": value,
            }
        )
    assert "MCP_SERVER_ALLOWED_HOSTS" in str(info.value)


def test_start_without_token_fails_before_serving(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() bricht mit Exit-Code != 0 ab und startet keinen Server."""
    import uvicorn

    from hoffmann_data.__main__ import main

    def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("uvicorn darf ohne gültiges Token nicht gestartet werden")

    monkeypatch.setattr(uvicorn, "run", must_not_run)
    monkeypatch.setattr(uvicorn.Server, "run", must_not_run, raising=False)
    monkeypatch.setattr(uvicorn.Server, "serve", must_not_run, raising=False)

    with pytest.raises(SystemExit) as info:
        main({})

    assert info.value.code not in (0, None)
    assert "MCP_SERVER_TOKEN" in capsys.readouterr().err
