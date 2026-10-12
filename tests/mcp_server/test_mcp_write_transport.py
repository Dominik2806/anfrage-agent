"""Tests des Transports je Pfad im Doppelbetrieb (F08, ADR 0005 Punkt 3).

Festlegungen: Auf /mcp und /mcp-write gelten dieselben Regeln. Beide Apps laufen zustandslos (stateless_http=True),
streamen (json_response=False) und prüfen Host und Origin gleich (421 und 403, nach dem Token). Die Weiche wählt
nach dem Pfad, den auch die Route der gewählten App sieht: Ein prozentkodierter Strich (/mcp%2Dwrite) ist derselbe
Pfad wie /mcp-write, und Weiche und Route können hier nicht auseinanderlaufen.

Ohne Datenbank. Rot, bis server.py und switch.py den Doppelbetrieb kennen (TDD).
"""

from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import BASE_URL, MCP_PATH, RPC_HEADERS, WRITE_PATH
from starlette.testclient import TestClient

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
PATHS = [MCP_PATH, WRITE_PATH]


def _post(
    client: TestClient,
    path: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] = TOOLS_LIST,
) -> Any:
    return client.post(path, json=payload, headers={**RPC_HEADERS, **(headers or {})})


@pytest.fixture
def own_headers(
    read_headers: dict[str, str], write_headers: dict[str, str]
) -> dict[str, dict[str, str]]:
    """Das Token je Pfad."""
    return {MCP_PATH: read_headers, WRITE_PATH: write_headers}


# Zustandslos und Streaming


@pytest.mark.parametrize("path", PATHS)
def test_both_paths_are_stateless(
    dual_client: TestClient, own_headers: dict[str, dict[str, str]], path: str
) -> None:
    """Ohne initialize, ohne Sitzung: kein Mcp-Session-Id in der Antwort, eine erfundene Sitzungskennung ändert nichts."""
    headers = own_headers[path]
    plain = _post(dual_client, path, headers)
    assert plain.status_code == 200
    assert "mcp-session-id" not in plain.headers
    initialized = _post(dual_client, path, headers, INITIALIZE)
    assert initialized.status_code == 200
    assert "mcp-session-id" not in initialized.headers
    invented = _post(dual_client, path, {**headers, "Mcp-Session-Id": "erfunden-0123456789"})
    assert invented.status_code == 200
    assert "mcp-session-id" not in invented.headers


@pytest.mark.parametrize("path", PATHS)
def test_both_paths_answer_as_an_event_stream(
    dual_client: TestClient, own_headers: dict[str, dict[str, str]], path: str
) -> None:
    """json_response=False (F10 braucht Streaming): Die Antwort ist ein Ereignisstrom, kein reines JSON."""
    response = _post(dual_client, path, own_headers[path])
    assert response.headers["content-type"].startswith("text/event-stream")


# Host und Origin


@pytest.mark.parametrize("path", PATHS)
def test_host_and_origin_are_checked_on_both_paths_after_a_valid_token(
    dual_client: TestClient, own_headers: dict[str, dict[str, str]], path: str
) -> None:
    headers = own_headers[path]
    assert _post(dual_client, path, {**headers, "Host": "evil.example"}).status_code == 421
    assert _post(dual_client, path, {**headers, "Origin": "http://evil.example"}).status_code == 403
    assert (
        _post(dual_client, path, {**headers, "Origin": "http://localhost:3000"}).status_code == 200
    )


@pytest.mark.parametrize("path", PATHS)
def test_the_token_is_checked_before_host_and_origin_on_both_paths(
    dual_client: TestClient, path: str
) -> None:
    assert _post(dual_client, path, {"Host": "evil.example"}).status_code == 401
    assert _post(dual_client, path, {"Origin": "http://evil.example"}).status_code == 401


def _non_loopback_client(
    make_app: Callable[..., Any], write_env: dict[str, str], hosts: str
) -> TestClient:
    app = make_app({**write_env, "MCP_SERVER_HOST": "0.0.0.0", "MCP_SERVER_ALLOWED_HOSTS": hosts})
    return TestClient(app, base_url="http://daten.example.org")


@pytest.mark.parametrize("path", PATHS)
def test_non_loopback_host_uses_allowed_hosts_on_both_paths(
    make_app: Callable[..., Any],
    write_env: dict[str, str],
    own_headers: dict[str, dict[str, str]],
    path: str,
) -> None:
    """Auf 0.0.0.0 gilt nur die ausdrücklich erlaubte Host-Liste, nicht die Loopback-Voreinstellung."""
    headers = own_headers[path]
    with _non_loopback_client(make_app, write_env, "daten.example.org") as client:
        assert _post(client, path, headers).status_code == 200
        assert _post(client, path, {**headers, "Host": "evil.example"}).status_code == 421
        assert _post(client, path, {**headers, "Host": "127.0.0.1:8000"}).status_code == 421


@pytest.mark.parametrize("path", PATHS)
def test_non_loopback_host_rejects_any_origin_on_both_paths(
    make_app: Callable[..., Any],
    write_env: dict[str, str],
    own_headers: dict[str, dict[str, str]],
    path: str,
) -> None:
    headers = own_headers[path]
    with _non_loopback_client(make_app, write_env, "daten.example.org") as client:
        assert _post(client, path, headers).status_code == 200
        for origin in ("http://daten.example.org", "http://evil.example", "http://localhost:3000"):
            assert _post(client, path, {**headers, "Origin": origin}).status_code == 403, origin


@pytest.mark.parametrize("path", PATHS)
def test_extra_allowed_hosts_apply_to_both_paths_on_loopback(
    make_app: Callable[..., Any],
    write_env: dict[str, str],
    own_headers: dict[str, dict[str, str]],
    path: str,
) -> None:
    """Dieselben Transport-Einstellungen für beide Apps: auch eine zusätzlich erlaubte Host-Angabe gilt auf beiden."""
    app = make_app({**write_env, "MCP_SERVER_ALLOWED_HOSTS": "daten.example.org"})
    headers = own_headers[path]
    with TestClient(app, base_url=BASE_URL) as client:
        assert _post(client, path, {**headers, "Host": "daten.example.org"}).status_code == 200
        assert _post(client, path, {**headers, "Host": "evil.example"}).status_code == 421


# Derselbe Pfad für Weiche und Route


def test_a_percent_encoded_dash_is_the_same_path(
    dual_client: TestClient, read_headers: dict[str, str], write_headers: dict[str, str]
) -> None:
    """/mcp%2Dwrite wird vor der App zu /mcp-write dekodiert: Die Weiche wählt die Schreib-App, und deren Route trifft.

    Weder gewinnt das Lese-Token über eine Schreibweise des Pfads, noch führt die Weiche zu einer Route, die der
    Weiche widerspricht. Doppelt kodiert (/mcp%252Dwrite) ist ein anderer Pfad: Lese-App, kein Treffer.
    """
    for encoded in ("/mcp%2Dwrite", "/mcp-writ%65", "/%6Dcp-write"):
        assert _post(dual_client, encoded, write_headers).status_code == 200, encoded
        assert _post(dual_client, encoded, read_headers).status_code == 401, encoded
        assert _post(dual_client, encoded).status_code == 401, encoded
    for other in ("/mcp%252Dwrite", "/mcp%2Fwrite"):
        assert _post(dual_client, other, write_headers).status_code == 401, other
        assert _post(dual_client, other, read_headers).status_code == 404, other
