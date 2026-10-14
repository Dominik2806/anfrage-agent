"""Port-Tests der Fortschrittsmeldungen gegen den Hoffmann-Datenservice (F10): echter Socket, echter SDK-Client.

Ausnahme zur Regel "kein Port" in mcp-server/CLAUDE.md (ADR 0007): uvicorn im Thread auf 127.0.0.1, Port 0, Socket vorab
gebunden (mcp_testkit.serve_on_loopback). Lokal ohne Sockets Skip, in GitHub Actions Fehler (serve_or_skip).
Je Transportweg ein Test (alter Weg = Handshake, moderner Weg = 2026-07-28); jeder Test ruft find_customer auf /mcp und
create_lead auf /mcp-write auf, mit dem jeweiligen Token.

Die Datenbank ist ein Double ohne Postgres. Sie wartet in einem spaeten Schritt (dritte Abfrage von find_customer, INSERT
von create_lead) auf ein threading.Event, das der progress_callback des Clients setzt: Kommt die erste Meldung erst mit dem
Ergebnis, bleibt `released` falsch (Zeitlimit 5 s). Ereignisbasiert, keine Schlafzeiten.
"""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_testkit import (
    LEAD_ARGUMENTS,
    MCP_PATH,
    WRITE_PATH,
    ScriptedDatabase,
    customer_handler,
    make_write_handler,
    serve_or_skip,
)

GATE_WAIT = 5
CLIENT_TIMEOUT = 15
FIND_ARGUMENTS = {"query": "erika@muster.example"}


@pytest.fixture
def serve() -> Iterator[Any]:
    with ExitStack() as stack:
        yield lambda app: serve_or_skip(stack, app)


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_the_real_server_delivers_progress_before_the_result_on_both_paths(
    serve: Any, write_env: dict[str, str], token: str, write_token: str, mode: str
) -> None:
    from hoffmann_data import crm, db
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    seen = {"read": threading.Event(), "write": threading.Event()}
    released: dict[str, bool] = {}

    def gate_read(text: str) -> None:
        if text == crm.ACTIVITIES_SQL:
            released["read"] = seen["read"].wait(GATE_WAIT)

    def gate_write(text: str) -> None:
        if text == db.INSERT_CUSTOMER_SQL:
            released["write"] = seen["write"].wait(GATE_WAIT)

    app = create_app(
        load_config(write_env),
        database=ScriptedDatabase(customer_handler, [], before_statement=gate_read),
        write_database=ScriptedDatabase(
            make_write_handler(), [], write=True, before_statement=gate_write
        ),
    )
    base = serve(app)
    calls: dict[str, list[tuple[float, float | None, str | None]]] = {"read": [], "write": []}

    async def call(which: str, path: str, secret: str, tool: str, arguments: dict[str, Any]) -> Any:
        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            calls[which].append((progress, total, message))
            seen[which].set()

        headers = {"Authorization": f"Bearer {secret}"}
        async with httpx2.AsyncClient(
            headers=headers, timeout=httpx2.Timeout(CLIENT_TIMEOUT)
        ) as http:
            transport = streamable_http_client(base + path, http_client=http)
            async with Client(transport, mode=mode) as client:
                return await client.call_tool(tool, arguments, progress_callback=on_progress)

    async def main() -> tuple[Any, Any]:
        found = await call("read", MCP_PATH, token, "find_customer", FIND_ARGUMENTS)
        created = await call("write", WRITE_PATH, write_token, "create_lead", LEAD_ARGUMENTS)
        return found, created

    found, created = asyncio.run(asyncio.wait_for(main(), timeout=40))
    assert found.is_error is False and created.is_error is False
    assert released == {"read": True, "write": True}, "die erste Meldung kam erst mit dem Ergebnis"
    assert calls["read"] == [
        (1, 3, "Kunde gesucht"),
        (2, 3, "Ansprechpersonen geladen"),
        (3, 3, "Verlauf geladen"),
    ]
    assert calls["write"] == [
        (1, 2, "Eingabe gepr\U000000fcft"),
        (2, 2, "Transaktion abgeschlossen"),
    ]
