"""Tests: Das Token taucht in keiner Meldung auf, und F06 braucht keine Datenbank (F06)."""

import logging
from pathlib import Path
from typing import Any

import pytest
from mcp_testkit import MCP_PATH, MCP_SERVER_DIR, RPC_HEADERS
from starlette.testclient import TestClient

BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.mark.parametrize(
    "secret",
    [
        "kurz-und-geheim",
        "x" * 20 + " " + "y" * 20,
        "z" * 40 + "\n",
    ],
    ids=["zu-kurz", "leerzeichen", "zeilenumbruch"],
)
def test_config_errors_do_not_contain_the_token(secret: str) -> None:
    from hoffmann_data.config import ConfigError, load_config

    with pytest.raises(ConfigError) as info:
        load_config({"MCP_SERVER_TOKEN": secret})
    message = str(info.value)
    assert secret.strip() not in message
    assert secret not in message
    assert repr(secret) not in message


def test_start_error_output_does_not_contain_the_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hoffmann_data.__main__ import main

    secret = "k" * 10 + " " + "k" * 30
    with pytest.raises(SystemExit):
        main({"MCP_SERVER_TOKEN": secret})
    captured = capsys.readouterr()
    assert "k" * 10 not in captured.out + captured.err


def test_token_is_in_no_response_and_no_log(
    client: TestClient,
    token: str,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wrong = "w" * len(token)
    caplog.set_level(logging.DEBUG)

    responses: list[Any] = [
        client.post(MCP_PATH, json=BODY, headers=RPC_HEADERS),
        client.post(
            MCP_PATH, json=BODY, headers={**RPC_HEADERS, "Authorization": f"Bearer {wrong}"}
        ),
        client.post(
            MCP_PATH, json=BODY, headers={**RPC_HEADERS, "Host": "evil.example", **auth_headers}
        ),
        client.post(
            MCP_PATH,
            json={
                **BODY,
                "method": "tools/call",
                "params": {"name": "get_product", "arguments": {}},
            },
            headers={**RPC_HEADERS, **auth_headers},
        ),
        client.post(
            MCP_PATH,
            json={**BODY, "method": "resources/read", "params": {"uri": "policy://rabatte"}},
            headers={**RPC_HEADERS, **auth_headers},
        ),
    ]

    for response in responses:
        assert token not in response.text
        assert wrong not in response.text
        assert token not in str(dict(response.headers))
        assert wrong not in str(dict(response.headers))

    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert token not in logged
    assert wrong not in logged
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert wrong not in captured.out + captured.err


def test_config_object_does_not_reveal_the_token_in_repr(token: str) -> None:
    from hoffmann_data.config import load_config

    config = load_config({"MCP_SERVER_TOKEN": token})
    assert token not in repr(config)
    assert token not in str(config)


def test_package_has_no_database_access() -> None:
    """F06 kennt keine Datenbank: weder Treiber noch Verbindungsadresse im Paket."""
    package = Path(MCP_SERVER_DIR) / "hoffmann_data"
    sources = sorted(package.rglob("*.py"))
    assert sources, "mcp-server/hoffmann_data enthält noch keinen Code"
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "psycopg" not in text, source
        assert "DATABASE_URL" not in text, source
        assert "asyncpg" not in text, source
