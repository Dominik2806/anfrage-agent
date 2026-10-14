"""Beleg fuer F10: beide Wege des SDKs (mcp 2.3.0) liefern Fortschritt gleich; das Zeitfenster des modernen Wegs.

Ohne Paketcode, mit einem eigenen Werkzeug des Tests (sync, `ctx: Context`, `anyio.from_thread.run`). Im Speicher
(Starlette-TestClient, kein Port), deshalb sagen die Tests etwas ueber Inhalt und Reihenfolge der Ereignisse, nicht
ueber Zeitpunkte.

- Alter Weg: kein Header MCP-Protocol-Version (oder eine Handshake-Version), zustandslos.
- Moderner Weg: Header MCP-Protocol-Version: 2026-07-28 plus Umschlagfelder in params._meta. Die Antwort beginnt als
  JSON und schaltet beim ersten Ereignis auf einen Strom um; schweigt der Aufruf laenger als _SSE_PING_INTERVAL
  (Standard 15 s, Modulvariable), wird der Strom trotzdem begonnen und mit Kommentarzeilen offen gehalten.
"""

import time
from typing import Any

import anyio.from_thread
import pytest
from mcp.server.mcpserver import Context
from mcp_testkit import (
    BASE_URL,
    MCP_PATH,
    RPC_HEADERS,
    modern_tool_call,
    parse_sse_events,
    probe_app,
)
from starlette.testclient import TestClient

TOKEN = "beleg-token-2"
MODERN_MODULE = "mcp.server._streamable_http_modern"


def _reporting_tool(ctx: Context) -> str:
    anyio.from_thread.run(ctx.report_progress, 1, 2, "erster Schritt")
    anyio.from_thread.run(ctx.report_progress, 2, 2, "zweiter Schritt")
    return "fertig"


def _silent_tool(ctx: Context) -> str:
    return "fertig"


def _slow_silent_tool(ctx: Context) -> str:
    time.sleep(0.8)
    return "fertig"


def _legacy_call(client: TestClient, token: Any) -> Any:
    meta = {} if token is None else {"progressToken": token}
    params: dict[str, Any] = {"name": "probe_progress", "arguments": {}}
    if meta:
        params["_meta"] = meta
    payload = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}
    return client.post(MCP_PATH, json=payload, headers=RPC_HEADERS)


def _modern_call(client: TestClient, token: Any) -> Any:
    payload, headers = modern_tool_call("probe_progress", progress_token=token)
    return client.post(MCP_PATH, json=payload, headers=headers)


CALLS = {"alt": _legacy_call, "modern": _modern_call}


@pytest.mark.parametrize("era", sorted(CALLS))
def test_both_eras_deliver_the_same_progress_events(era: str) -> None:
    """Gleiche Meldungen in gleicher Reihenfolge vor dem Ergebnis, auf beiden Wegen."""
    with TestClient(probe_app(_reporting_tool), base_url=BASE_URL) as client:
        response = CALLS[era](client, TOKEN)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse_events(response.text)
    assert [event.get("method") for event in events] == [
        "notifications/progress",
        "notifications/progress",
        None,
    ]
    assert events[0]["params"] == {
        "progressToken": TOKEN,
        "progress": 1,
        "total": 2,
        "message": "erster Schritt",
    }
    assert events[1]["params"]["progress"] == 2
    assert events[2]["result"]["content"][0]["text"] == "fertig"
    assert events[2]["result"].get("isError") is not True


@pytest.mark.parametrize("era", sorted(CALLS))
def test_both_eras_send_no_progress_without_a_token(era: str) -> None:
    """Ohne progressToken kein Fortschritt und kein Fehler, auf beiden Wegen (das Werkzeug ruft trotzdem report_progress)."""
    with TestClient(probe_app(_reporting_tool), base_url=BASE_URL) as client:
        response = CALLS[era](client, None)
    assert response.status_code == 200, response.text
    assert "notifications/progress" not in response.text
    events = parse_sse_events(response.text) if "data:" in response.text else [response.json()]
    assert len(events) == 1
    assert events[0]["result"]["content"][0]["text"] == "fertig"


def test_modern_era_answers_json_when_the_call_finishes_without_any_event() -> None:
    """Moderner Weg: Schliesst der Aufruf in der Frist ohne Ereignis ab, ist die Antwort ein JSON-Dokument, kein Strom."""
    with TestClient(probe_app(_silent_tool), base_url=BASE_URL) as client:
        response = _modern_call(client, TOKEN)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["result"]["content"][0]["text"] == "fertig"


def test_modern_era_commits_to_a_stream_when_the_call_is_silent_past_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moderner Weg: Schweigt der Aufruf laenger als das Fenster, beginnt der Strom mit Kommentarzeilen, dann das Ergebnis."""
    monkeypatch.setattr(f"{MODERN_MODULE}._SSE_PING_INTERVAL", 0.2)
    with TestClient(probe_app(_slow_silent_tool), base_url=BASE_URL) as client:
        response = _modern_call(client, TOKEN)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert ": ping" in response.text
    events = parse_sse_events(response.text)
    assert [event.get("method") for event in events] == [None]
    assert events[0]["result"]["content"][0]["text"] == "fertig"


def test_modern_era_commits_to_a_stream_at_the_first_event_not_after_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moderner Weg: Eine Meldung vor Ablauf des Fensters schaltet sofort auf den Strom (Fenster hier sehr gross)."""
    monkeypatch.setattr(f"{MODERN_MODULE}._SSE_PING_INTERVAL", 600.0)
    with TestClient(probe_app(_reporting_tool), base_url=BASE_URL) as client:
        response = _modern_call(client, TOKEN)
    # Haette der Server das Fenster (600 s) abgewartet, kaeme die Antwort nicht zurueck; hier genuegt der Inhalt
    assert response.headers["content-type"].startswith("text/event-stream")
    assert ": ping" not in response.text
    assert [event.get("method") for event in parse_sse_events(response.text)] == [
        "notifications/progress",
        "notifications/progress",
        None,
    ]
