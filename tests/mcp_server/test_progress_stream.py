"""Tests des Stroms der Fortschrittsmeldungen mit den echten Werkzeugen (F10), ASGI ohne Port.

Die App laeuft direkt als ASGI (mcp_testkit.AsgiRunner), die Datenbank ist ein Double (ScriptedDatabase) ohne Postgres.
Festgelegt (ADR 0007):
- Die erste Meldung kommt vor dem Ergebnis: Das Werkzeug wartet in der Datenbank (threading.Event mit Zeitlimit), bis
  `send` die erste notifications/progress gesehen hat. Kommt sie erst mit dem Ergebnis, bleibt `released` falsch.
- Ein Client, der nicht liest (moderner Weg, Strom ohne Puffer), haelt das Werkzeug nicht laenger als das Zeitlimit je
  Meldung auf: Die lesende Verbindung wird geschlossen, bevor der Client wieder liest; danach kommt keine weitere Meldung.
- Geht der Client mitten im Aufruf, laeuft das Schreib-Werkzeug zu Ende (Commit wie ohne Fortschritt); ein neuer Aufruf mit
  denselben Werten trifft die Dublettenerkennung aus F08 (customer_created=false, kein weiterer INSERT).

Jede Wartezeit hat ein hartes Zeitlimit.
"""

import asyncio
import threading
from typing import Any

import pytest
from mcp_testkit import (
    LEAD_ARGUMENTS,
    MCP_PATH,
    WRITE_PATH,
    AsgiRunner,
    ScriptedDatabase,
    customer_handler,
    make_write_handler,
    parse_sse_events,
    progress_events,
    tool_call_request,
)

WAIT = 10
GATE_WAIT = 3  # Sperre in der Datenbank: kurz genug, dass ein roter Test nicht lange haengt
PROGRESS_TOKEN = "fortschritt-token-0815"
FIND_ARGUMENTS = {"query": "erika@muster.example"}


def _app(
    env: dict[str, str],
    *,
    read: ScriptedDatabase | None = None,
    write: ScriptedDatabase | None = None,
) -> Any:
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    return create_app(load_config(env), database=read, write_database=write)


def _has_progress(message: dict[str, Any]) -> bool:
    return b"notifications/progress" in message.get("body", b"")


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize("tool", ["find_customer", "create_lead"])
def test_the_first_progress_arrives_before_the_tool_finishes(
    write_env: dict[str, str], token: str, write_token: str, tool: str, era: str
) -> None:
    from hoffmann_data import crm, db

    seen = threading.Event()
    released: list[bool] = []

    def gate_read(text: str) -> None:
        if text == crm.ACTIVITIES_SQL:  # dritter Schritt von find_customer
            released.append(seen.wait(GATE_WAIT))

    def gate_write(text: str) -> None:
        if text == db.INSERT_CUSTOMER_SQL:  # in der Transaktion, nach der ersten Meldung
            released.append(seen.wait(GATE_WAIT))

    read = ScriptedDatabase(customer_handler, [], before_statement=gate_read)
    write = ScriptedDatabase(make_write_handler(), [], write=True, before_statement=gate_write)
    path, arguments, secret = (
        (MCP_PATH, FIND_ARGUMENTS, token)
        if tool == "find_customer"
        else (WRITE_PATH, LEAD_ARGUMENTS, write_token)
    )
    payload, headers = tool_call_request(
        era, tool, arguments, progress_token=PROGRESS_TOKEN, authorization=secret
    )

    async def on_send(message: dict[str, Any]) -> None:
        if _has_progress(message):
            seen.set()

    async def main() -> Any:
        async with AsgiRunner(_app(write_env, read=read, write=write)) as runner:
            return await asyncio.wait_for(
                runner.post(path, payload, headers, on_send=on_send), timeout=WAIT * 2
            )

    response = asyncio.run(main())
    assert response.status == 200
    assert released == [True], "die erste Meldung kam erst mit dem Ergebnis"
    chunks = response.chunks
    first = next(i for i, chunk in enumerate(chunks) if b"notifications/progress" in chunk)
    result = next(i for i, chunk in enumerate(chunks) if b'"result"' in chunk)
    assert first < result


def test_a_client_that_does_not_read_cannot_hold_the_tool_longer_than_the_timeout(
    write_env: dict[str, str], token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data import progress

    # 1,0 s statt 0,3 s: Auf einem ausgelasteten Rechner muss die erste Meldung sicher vor dem Zeitlimit ankommen
    monkeypatch.setattr(progress, "PROGRESS_TIMEOUT_SECONDS", 1.0)
    finished = threading.Event()
    timeline: list[str] = []
    read = ScriptedDatabase(customer_handler, timeline, on_end=lambda kind: finished.set())
    payload, headers = tool_call_request(
        "modern",
        "find_customer",
        FIND_ARGUMENTS,
        progress_token=PROGRESS_TOKEN,
        authorization=token,
    )

    async def main() -> tuple[bool, Any]:
        gate = asyncio.Event()

        async def on_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("more_body"):
                await gate.wait()  # der Client liest nicht

        try:
            async with AsgiRunner(_app(write_env, read=read)) as runner:
                request = asyncio.create_task(
                    runner.post(MCP_PATH, payload, headers, on_send=on_send)
                )
                done_while_blocked = await asyncio.to_thread(finished.wait, WAIT)
                gate.set()
                return done_while_blocked, await asyncio.wait_for(request, WAIT)
        finally:
            gate.set()

    done_while_blocked, response = asyncio.run(main())
    assert done_while_blocked, "die Verbindung blieb offen, solange der Client nicht las"
    assert timeline.count("sql:SELECT") == 3 and timeline[-1] == "close"
    events = parse_sse_events(response.text)
    assert len(progress_events(events)) == 1, "nach dem Zeitlimit darf nichts mehr gesendet werden"
    assert events[-1]["result"].get("isError") is not True


@pytest.mark.parametrize("era", ["alt", "modern"])
def test_a_client_that_leaves_mid_call_does_not_stop_the_write_and_a_retry_hits_the_duplicate_check(
    write_env: dict[str, str], write_token: str, era: str
) -> None:
    from hoffmann_data import db

    committed = threading.Event()
    release = threading.Event()
    stored: set[str] = set()
    staged: list[str] = []
    timeline: list[str] = []

    def handler(text: str, params: Any) -> list[dict[str, Any]]:
        if text == db.FIND_LEAD_MATCHES_SQL:
            value = 9 if params["domain"] in stored else None
            return [
                {
                    "domain_customer_id": value,
                    "name_customer_id": value,
                    "contact_customer_id": value,
                }
            ]
        if text == db.INSERT_CUSTOMER_SQL:
            staged.append(params["domain"])
            return [{"id": 9}]
        return []

    def on_end(kind: str) -> None:
        if kind == "commit":
            stored.update(staged)
            committed.set()
        staged.clear()

    def before(text: str) -> None:
        if text == db.INSERT_CUSTOMER_SQL:
            release.wait(WAIT)

    write = ScriptedDatabase(handler, timeline, write=True, before_statement=before, on_end=on_end)
    payload, headers = tool_call_request(
        era, "create_lead", LEAD_ARGUMENTS, progress_token=PROGRESS_TOKEN, authorization=write_token
    )

    async def main() -> tuple[int, Any]:
        first_seen = threading.Event()
        disconnect = asyncio.Event()

        async def on_send(message: dict[str, Any]) -> None:
            if _has_progress(message):
                first_seen.set()

        try:
            async with AsgiRunner(_app(write_env, write=write)) as runner:
                request = asyncio.create_task(
                    runner.post(
                        WRITE_PATH, payload, headers, on_send=on_send, disconnect=disconnect
                    )
                )
                assert await asyncio.to_thread(first_seen.wait, WAIT), (
                    "keine Meldung vor der Transaktion"
                )
                disconnect.set()  # der Client geht, das Werkzeug steht noch vor dem INSERT
                await asyncio.wait_for(request, WAIT)
                release.set()
                assert await asyncio.to_thread(committed.wait, WAIT), (
                    "das Werkzeug lief nicht zu Ende"
                )
                before_second = len(timeline)
                second = await asyncio.wait_for(
                    runner.post(WRITE_PATH, payload, headers), timeout=WAIT
                )
                return before_second, second
        finally:
            release.set()

    before_second, second = asyncio.run(main())
    assert timeline[:before_second].count("sql:INSERT") == 2
    assert timeline[:before_second][-1] == "commit"
    assert timeline[before_second:] == ["open", "sql:SELECT", "commit"]
    events = parse_sse_events(second.text)
    assert events[-1]["result"]["structuredContent"] == {
        "customer_domain": "neu-gmbh.example",
        "customer_created": False,
        "contact_created": False,
    }
