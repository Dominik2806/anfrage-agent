"""Beleg fuer F10: anyio.from_thread.run funktioniert im SDK-Thread eines synchronen Werkzeugs.

Ohne Paketcode: Das Werkzeug gehoert dem Test, es hat einen Parameter `ctx: Context` und sendet seinen Fortschritt
mit `anyio.from_thread.run(ctx.report_progress, ...)` aus dem Worker-Thread (ADR 0007, Entscheidung 1). Der Test belegt das
Verhalten des SDK (mcp 2.3.0), statt es aus dem Quelltext zu folgern. Er laeuft im Speicher (kein Port) auf dem
alten Weg (ohne Header MCP-Protocol-Version). Ist er rot, kann ein synchrones Werkzeug nicht aus seinem Worker-Thread senden (Voraussetzung von ADR 0007).
"""

import asyncio
import json
import threading
from typing import Any

import anyio.from_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_testkit import BASE_URL, MCP_PATH, RPC_HEADERS
from starlette.testclient import TestClient

TOOL = "probe_progress"
TOKEN = "beleg-token-1"


def _events(text: str) -> list[dict[str, Any]]:
    """Alle data:-Ereignisse eines Ereignisstroms als JSON, in der Reihenfolge des Stroms."""
    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def _client(seen: dict[str, Any]) -> TestClient:
    server = MCPServer("beleg")

    @server.tool(name=TOOL)
    def probe_progress(ctx: Context) -> str:
        try:
            asyncio.get_running_loop()
            seen["loop_in_thread"] = True
        except RuntimeError:
            seen["loop_in_thread"] = False
        seen["thread"] = threading.current_thread().name
        anyio.from_thread.run(ctx.report_progress, 1, 2, "erster Schritt")
        anyio.from_thread.run(ctx.report_progress, 2, 2, "zweiter Schritt")
        return "fertig"

    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=False,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=["http://127.0.0.1:*"],
        ),
        host="127.0.0.1",
    )
    return TestClient(app, base_url=BASE_URL)


def _call(client: TestClient, meta: dict[str, Any] | None) -> Any:
    params: dict[str, Any] = {"name": TOOL, "arguments": {}}
    if meta is not None:
        params["_meta"] = meta
    payload = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}
    return client.post(MCP_PATH, json=payload, headers=RPC_HEADERS)


def test_from_thread_run_sends_progress_from_a_sync_tool() -> None:
    """Mit progressToken kommen beide Meldungen im Strom, vor dem Ergebnis, mit Token, Wert, Summe und Text."""
    seen: dict[str, Any] = {}
    with _client(seen) as client:
        response = _call(client, {"progressToken": TOKEN})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    methods = [event.get("method") for event in events]
    assert methods == ["notifications/progress", "notifications/progress", None], methods
    first, second, final = events
    assert first["params"] == {
        "progressToken": TOKEN,
        "progress": 1,
        "total": 2,
        "message": "erster Schritt",
    }
    assert second["params"]["progress"] == 2
    assert second["params"]["message"] == "zweiter Schritt"
    assert final["id"] == 7
    assert final["result"].get("isError") is not True
    assert final["result"]["content"][0]["text"] == "fertig"
    # Das Werkzeug lief in einem Worker-Thread ohne eigene Ereignisschleife (sonst belegte der Test nichts)
    assert seen["loop_in_thread"] is False


def test_from_thread_run_without_progress_token_sends_nothing_and_does_not_fail() -> None:
    """Ohne progressToken ist report_progress ein No-op: ein Ereignis (das Ergebnis), kein Fehler."""
    seen: dict[str, Any] = {}
    with _client(seen) as client:
        response = _call(client, None)
    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert [event.get("method") for event in events] == [None], events
    assert events[0]["result"].get("isError") is not True
    assert events[0]["result"]["content"][0]["text"] == "fertig"
    assert "notifications/progress" not in response.text
    assert seen["loop_in_thread"] is False
