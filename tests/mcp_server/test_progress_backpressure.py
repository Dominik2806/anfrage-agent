"""Beleg fuer F10: Ein Client, der nicht liest, blockiert auf dem modernen Weg den sendenden Thread (Strom ohne Puffer).

Ohne Paketcode und ohne Port: Die App laeuft direkt als ASGI (AsgiRunner aus mcp_testkit). Das eigene `send` des
Tests haelt den Strom an, wie ein Client, der nicht liest. Belegt werden zwei Eigenschaften, an denen das Zeitlimit je
Meldung des Plans haengt:

1. Die erste Meldung wird angenommen (die Schleife wartet auf sie, bevor sie den Strom beginnt), die zweite blockiert
   `anyio.from_thread.run`, solange der Client nicht liest.
2. Ein Zeitlimit in der uebergebenen Coroutine (`anyio.fail_after`) beendet das Warten im Thread mit TimeoutError; der
   Thread laeuft weiter und kann nach dem Freigeben des Stroms wieder senden.

Jede Wartezeit hat ein hartes Zeitlimit, damit ein roter Test nicht haengt.
"""

import asyncio
import threading
import time
from typing import Any

import anyio
import anyio.from_thread
from mcp.server.mcpserver import Context
from mcp_testkit import MCP_PATH, AsgiRunner, modern_tool_call, parse_sse_events, probe_app

TOKEN = "beleg-token-3"
WAIT = 10


def _is_stream_chunk(message: dict[str, Any]) -> bool:
    return message["type"] == "http.response.body" and bool(message.get("more_body"))


def test_modern_era_sender_blocks_until_the_client_reads() -> None:
    first_done = threading.Event()
    second_done = threading.Event()

    def tool(ctx: Context) -> str:
        anyio.from_thread.run(ctx.report_progress, 1, 3, "eins")
        first_done.set()
        anyio.from_thread.run(ctx.report_progress, 2, 3, "zwei")
        second_done.set()
        return "fertig"

    async def main() -> tuple[bool, bool, Any]:
        gate = asyncio.Event()

        async def on_send(message: dict[str, Any]) -> None:
            if _is_stream_chunk(message):
                await gate.wait()  # der Client liest nicht

        payload, headers = modern_tool_call("probe_progress", progress_token=TOKEN)
        try:
            async with AsgiRunner(probe_app(tool)) as runner:
                request = asyncio.create_task(
                    runner.post(MCP_PATH, payload, headers, on_send=on_send)
                )
                first = await asyncio.to_thread(first_done.wait, WAIT)
                second_while_blocked = await asyncio.to_thread(second_done.wait, 1.5)
                gate.set()
                second_after_release = await asyncio.to_thread(second_done.wait, WAIT)
                response = await asyncio.wait_for(request, WAIT)
        finally:
            gate.set()
        assert response.status == 200
        assert first, "die erste Meldung wurde nicht angenommen"
        assert second_after_release, "die zweite Meldung kam auch nach dem Freigeben nicht an"
        return second_while_blocked, second_after_release, response

    second_while_blocked, _, response = asyncio.run(main())
    assert second_while_blocked is False, "die zweite Meldung blockierte nicht"
    events = parse_sse_events(response.text)
    assert [event.get("method") for event in events] == [
        "notifications/progress",
        "notifications/progress",
        None,
    ]


def test_a_timeout_inside_the_coroutine_frees_the_thread_and_it_can_send_again() -> None:
    outcome: dict[str, Any] = {}
    released = threading.Event()
    second_finished = threading.Event()

    def tool(ctx: Context) -> str:
        async def bounded(step: int, text: str) -> None:
            with anyio.fail_after(0.3):
                await ctx.report_progress(step, 3, text)

        anyio.from_thread.run(bounded, 1, "eins")
        started = time.monotonic()
        try:
            anyio.from_thread.run(bounded, 2, "zwei")
            outcome["second"] = "gesendet"
        except TimeoutError:
            outcome["second"] = "zeitlimit"
        outcome["second_seconds"] = time.monotonic() - started
        second_finished.set()
        outcome["released"] = released.wait(WAIT)
        try:
            anyio.from_thread.run(bounded, 3, "drei")
            outcome["third"] = "gesendet"
        except Exception as error:  # noqa: BLE001 - Beleg: die Klasse wird gemeldet
            outcome["third"] = type(error).__name__
        return "fertig"

    async def main() -> Any:
        gate = asyncio.Event()

        async def on_send(message: dict[str, Any]) -> None:
            if _is_stream_chunk(message):
                await gate.wait()

        payload, headers = modern_tool_call("probe_progress", progress_token=TOKEN)
        try:
            async with AsgiRunner(probe_app(tool)) as runner:
                request = asyncio.create_task(
                    runner.post(MCP_PATH, payload, headers, on_send=on_send)
                )
                assert await asyncio.to_thread(second_finished.wait, WAIT), (
                    "der Thread blieb haengen"
                )
                gate.set()
                released.set()
                return await asyncio.wait_for(request, WAIT)
        finally:
            gate.set()
            released.set()

    response = asyncio.run(main())
    assert outcome["second"] == "zeitlimit", outcome
    assert outcome["second_seconds"] < 3, outcome
    assert outcome["released"] is True, outcome
    assert outcome["third"] == "gesendet", outcome
    events = parse_sse_events(response.text)
    messages = [
        e["params"]["message"] for e in events if e.get("method") == "notifications/progress"
    ]
    assert messages == ["eins", "drei"], messages
    assert events[-1]["result"]["content"][0]["text"] == "fertig"
