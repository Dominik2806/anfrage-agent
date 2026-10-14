"""Beleg fuer F10: der SDK-Client (mcp.Client) erhaelt Fortschritt ueber einen echten Socket, auf beiden Wegen.

Ohne Paketcode: ein eigenes Werkzeug des Tests, uvicorn im Thread auf 127.0.0.1 (Port 0, Socket vorab gebunden, siehe
mcp_testkit.serve_on_loopback). Das Werkzeug sendet die erste Meldung und wartet dann, bis der Client sie in seinem
`progress_callback` gesehen hat (threading.Event mit Zeitlimit), erst danach sendet es die zweite und das Ergebnis.
Kommt die erste Meldung erst mit dem Ergebnis, laeuft das Werkzeug in sein Zeitlimit und `released` bleibt falsch.

Erlaubt die Umgebung keinen Socket, wird der Test lokal uebersprungen; in GitHub Actions ist das ein Fehler (Muster wie
TEST_DATABASE_URL, siehe mcp_testkit.serve_or_skip). Der Client benutzt httpx2, eine Abhaengigkeit des SDKs (keine neue
Abhaengigkeit).
"""

import asyncio
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any

import anyio.from_thread
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import Context
from mcp_testkit import MCP_PATH, probe_app, serve_or_skip

TOOL = "probe_progress"
CLIENT_TIMEOUT = 15


@pytest.fixture
def serve() -> Iterator[Callable[[Any], str]]:
    """Fabrik: startet die App auf Loopback und gibt die URL des Endpunkts zurueck; ohne Sockets Skip (lokal) oder Fehler (CI)."""
    with ExitStack() as stack:
        yield lambda app: serve_or_skip(stack, app) + MCP_PATH


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
def test_sdk_client_receives_progress_before_the_result_over_a_real_socket(
    serve: Callable[[Any], str], mode: str
) -> None:
    first_seen = threading.Event()
    seen: dict[str, Any] = {}

    def tool(ctx: Context) -> str:
        anyio.from_thread.run(ctx.report_progress, 1, 2, "erster Schritt")
        seen["released"] = first_seen.wait(10)
        anyio.from_thread.run(ctx.report_progress, 2, 2, "zweiter Schritt")
        return "fertig"

    calls: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        calls.append((progress, total, message))
        first_seen.set()

    url = serve(probe_app(tool, TOOL))

    async def main() -> Any:
        async with httpx2.AsyncClient(timeout=httpx2.Timeout(CLIENT_TIMEOUT)) as http:
            transport = streamable_http_client(url, http_client=http)
            async with Client(transport, mode=mode) as client:
                return await client.call_tool(TOOL, {}, progress_callback=on_progress)

    result = asyncio.run(asyncio.wait_for(main(), timeout=30))
    assert result.is_error is False
    assert result.content[0].text == "fertig"  # type: ignore[union-attr]
    assert seen["released"] is True, "die erste Meldung kam erst mit dem Ergebnis"
    assert calls == [(1, 2, "erster Schritt"), (2, 2, "zweiter Schritt")]
