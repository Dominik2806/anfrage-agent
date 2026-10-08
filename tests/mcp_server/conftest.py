"""Fixtures für die Tests des Datenservice hoffmann-data (F06).

Die Tests brauchen weder Datenbank noch Netzwerk: Die ASGI-App läuft im Speicher hinter dem
Starlette-TestClient (kein Port wird geöffnet). Der Ordner heißt tests/mcp_server und nicht
tests/mcp, weil ein Ordner "mcp" das SDK-Paket überdecken könnte.

Der Code unter mcp-server/hoffmann_data wird erst in den Fixtures und Tests importiert, nicht beim
Einlesen der Datei. So scheitert jeder Test einzeln mit "No module named 'hoffmann_data'", solange der
Code fehlt. Konstanten und Hilfen stehen in mcp_testkit.py (dort kommt auch mcp-server in sys.path).

Der TestClient nutzt base_url mit 127.0.0.1:8000. Der Standardwert "testserver" würde vom
Host-Schutz des SDKs mit 421 abgelehnt.
"""

import secrets
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from mcp_testkit import BASE_URL, MCP_PATH, RPC_HEADERS, parse_rpc_body
from starlette.testclient import TestClient


@pytest.fixture
def token() -> str:
    """Zufälliges Test-Token je Test: 43 Zeichen, nur URL-sichere Zeichen, keine Leerzeichen."""
    return secrets.token_urlsafe(32)


@pytest.fixture
def make_app() -> Callable[..., Any]:
    """Baut die ASGI-App aus einer Umgebung (dict), ohne die echte Umgebung zu lesen."""

    def _make(env: dict[str, str]) -> Any:
        from hoffmann_data.config import load_config
        from hoffmann_data.server import create_app

        return create_app(load_config(env))

    return _make


@pytest.fixture
def app(make_app: Callable[..., Any], token: str) -> Any:
    return make_app({"MCP_SERVER_TOKEN": token})


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    """TestClient mit laufendem Lifespan (der Session-Manager des SDKs startet darin)."""
    with TestClient(app, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def rpc(client: TestClient, auth_headers: dict[str, str]) -> Callable[..., dict[str, Any]]:
    """Schickt rohes JSON-RPC mit gültigem Token an /mcp und gibt die geparste Antwort zurück.

    Das geht ohne initialize, weil der Server zustandslos läuft (stateless_http=True, ADR 0003).
    Das ist die Stelle, die Auth und Protokoll gemeinsam prüft; mcp.Client in-process würde die
    Auth umgehen.
    """
    counter = iter(range(1, 10_000))

    def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": next(counter), "method": method, "params": params or {}}
        response = client.post(MCP_PATH, json=payload, headers={**RPC_HEADERS, **auth_headers})
        assert response.status_code == 200, response.text
        return parse_rpc_body(response)

    return _call
